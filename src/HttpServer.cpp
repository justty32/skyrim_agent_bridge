#include "HttpServer.h"

#include <atomic>
#include <cctype>
#include <cstdlib>
#include <chrono>
#include <algorithm>
#include <thread>
#include <cerrno>

#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <fcntl.h>
using SOCKET = int;
constexpr int INVALID_SOCKET = -1, SOCKET_ERROR = -1, SD_SEND = SHUT_WR;
using BOOL = int;
constexpr int TRUE = 1;
inline int closesocket(int fd) { return ::close(fd); }
namespace SKSE::log { template<class... T> void error(T&&...) {} template<class... T> void info(T&&...) {} }
#endif

namespace {
    constexpr std::size_t kMaxRequestBytes = 1 << 20;   // 1 MiB — /console payloads are tiny
    constexpr int kAcceptPollMs = 200;                  // how often the accept loop checks for Stop()

    std::unordered_map<std::string, Http::Handler> g_routes;    // key: "GET /state"
    std::atomic<bool> g_running{ false };
    std::atomic<SOCKET> g_listen{ INVALID_SOCKET };
    std::thread g_thread;
    std::function<void()> g_onStop;
    bool g_wsaStarted = false;

    int SocketError() {
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
        return errno;
#else
        return ::WSAGetLastError();
#endif
    }
    bool RetrySocketError() {
        const int error = SocketError();
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
        return error == EAGAIN || error == EWOULDBLOCK || error == EINTR;
#else
        return error == WSAEWOULDBLOCK || error == WSAEINTR;
#endif
    }
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
    std::atomic<int> g_retryReceive{0}, g_retrySend{0};
    std::atomic<bool> g_failNonblocking{false};
#endif
    int Receive(SOCKET client, char* data, int size) {
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
        if (g_retryReceive.exchange(0)) { errno = EINTR; return -1; }
#endif
        return ::recv(client, data, size, 0);
    }
    int SendSocket(SOCKET client, const char* data, int size, int flags) {
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
        if (g_retrySend.exchange(0)) { errno = EWOULDBLOCK; return -1; }
#endif
        return ::send(client, data, size, flags);
    }
    bool SetNonblocking(SOCKET client) {
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
        if (g_failNonblocking.exchange(false)) return false;
        const int flags = ::fcntl(client, F_GETFL);
        return flags != -1 && ::fcntl(client, F_SETFL, flags | O_NONBLOCK) != -1;
#else
        u_long nonblocking = 1;
        return ::ioctlsocket(client, FIONBIO, &nonblocking) == 0;
#endif
    }
    void CleanupSockets() {
#ifndef AGENT_BRIDGE_HTTP_HOST_TEST
        ::WSACleanup();
#endif
    }
    using Deadline = std::chrono::steady_clock::time_point;
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
    constexpr auto kIoDeadline = std::chrono::milliseconds(350);
#else
    constexpr auto kIoDeadline = std::chrono::seconds(5);
#endif
    // Nonblocking sockets plus short select polls bound the whole transfer,
    // even when a peer trickles bytes forever, and observe Stop promptly.
    bool Ready(SOCKET socket, bool writing, Deadline deadline)
    {
        while (g_running.load() && std::chrono::steady_clock::now() < deadline) {
            fd_set fds;
            FD_ZERO(&fds);
            FD_SET(socket, &fds);
            timeval tv{0, 20000};
            const int result = ::select(static_cast<int>(socket + 1), writing ? nullptr : &fds,
                writing ? &fds : nullptr, nullptr, &tv);
            if (result > 0) return true;
            if (result < 0 && !RetrySocketError()) return false;
        }
        return false;
    }

    std::string UrlDecode(std::string_view in)
    {
        std::string out;
        out.reserve(in.size());
        for (std::size_t i = 0; i < in.size(); ++i) {
            if (in[i] == '+') {
                out.push_back(' ');
            } else if (in[i] == '%' && i + 2 < in.size()) {
                auto hex = [](char c) -> int {
                    if (c >= '0' && c <= '9') return c - '0';
                    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
                    return -1;
                };
                const int hi = hex(in[i + 1]);
                const int lo = hex(in[i + 2]);
                if (hi >= 0 && lo >= 0) {
                    out.push_back(static_cast<char>((hi << 4) | lo));
                    i += 2;
                    continue;
                }
                out.push_back(in[i]);
            } else {
                out.push_back(in[i]);
            }
        }
        return out;
    }

    void ParseTarget(std::string_view target, Http::Request& req)
    {
        const auto q = target.find('?');
        if (q == std::string_view::npos) {
            req.path = UrlDecode(target);
            return;
        }
        req.path = UrlDecode(target.substr(0, q));

        std::string_view rest = target.substr(q + 1);
        while (!rest.empty()) {
            const auto amp = rest.find('&');
            std::string_view pair = (amp == std::string_view::npos) ? rest : rest.substr(0, amp);
            const auto eq = pair.find('=');
            if (eq != std::string_view::npos) {
                req.query[UrlDecode(pair.substr(0, eq))] = UrlDecode(pair.substr(eq + 1));
            } else if (!pair.empty()) {
                req.query[UrlDecode(pair)] = "";
            }
            if (amp == std::string_view::npos) break;
            rest = rest.substr(amp + 1);
        }
    }

    // Reads one request off the socket. Headers first, then exactly
    // Content-Length bytes of body — a short recv() mid-body is normal and must
    // not be mistaken for the end of the request.
    bool ReadRequest(SOCKET client, Http::Request& req)
    {
        const auto deadline = std::chrono::steady_clock::now() + kIoDeadline;
        std::string buf;
        char chunk[4096];
        std::size_t headerEnd = std::string::npos;

        while (headerEnd == std::string::npos) {
            if (!Ready(client, false, deadline)) return false;
            const int n = Receive(client, chunk, sizeof(chunk));
            if (n < 0 && RetrySocketError()) continue;
            if (n <= 0) return false;
            buf.append(chunk, static_cast<std::size_t>(n));
            if (buf.size() > kMaxRequestBytes) return false;
            headerEnd = buf.find("\r\n\r\n");
        }

        const std::string head = buf.substr(0, headerEnd);
        const auto eol = head.find("\r\n");
        const std::string requestLine = head.substr(0, eol == std::string::npos ? head.size() : eol);

        const auto sp1 = requestLine.find(' ');
        if (sp1 == std::string::npos) return false;
        const auto sp2 = requestLine.find(' ', sp1 + 1);
        if (sp2 == std::string::npos) return false;

        req.method = requestLine.substr(0, sp1);
        ParseTarget(std::string_view{ requestLine }.substr(sp1 + 1, sp2 - sp1 - 1), req);

        // Content-Length, case-insensitively.
        std::size_t contentLength = 0;
        {
            std::string lower = head;
            for (auto& c : lower) c = static_cast<char>(::tolower(static_cast<unsigned char>(c)));
            const auto pos = lower.find("\r\ncontent-length:");
            if (pos != std::string::npos) {
                contentLength = static_cast<std::size_t>(std::strtoul(head.c_str() + pos + 2 + 15, nullptr, 10));
                if (contentLength > kMaxRequestBytes) return false;
            }
        }

        req.body = buf.substr(headerEnd + 4);
        while (req.body.size() < contentLength) {
            if (!Ready(client, false, deadline)) return false;
            const int n = Receive(client, chunk, sizeof(chunk));
            if (n < 0 && RetrySocketError()) continue;
            if (n <= 0) return false;
            req.body.append(chunk, static_cast<std::size_t>(n));
        }
        req.body.resize(contentLength);
        return true;
    }

    bool SendAll(SOCKET client, const char* data, std::size_t len, Deadline deadline)
    {
        std::size_t sent = 0;
        while (sent < len) {
            if (!Ready(client, true, deadline)) return false;
#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
            constexpr int flags = MSG_NOSIGNAL;
#else
            constexpr int flags = 0;
#endif
            const int n = SendSocket(client, data + sent,
                static_cast<int>(std::min(len - sent, std::size_t{65536})), flags);
            if (n < 0 && RetrySocketError()) continue;
            if (n <= 0) return false;
            sent += static_cast<std::size_t>(n);
        }
        return true;
    }

    void SendResponse(SOCKET client, const Http::Response& resp)
    {
        const bool binary = resp.binary;
        if (binary && resp.bytes.size() > Http::Response::MaxBinaryBytes) {
            SendResponse(client, Http::Response::Error(500, "binary response exceeds capacity"));
            return;
        }
        auto validValue = [](const std::string& value) {
            return std::all_of(value.begin(), value.end(), [](unsigned char c) { return c >= 32 && c != 127; });
        };
        if (binary && (resp.mime.empty() || !validValue(resp.mime))) {
            SendResponse(client, Http::Response::Error(500, "invalid binary MIME"));
            return;
        }
        const std::string body = binary ? std::string{} :
            resp.body.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
        const auto size = binary ? resp.bytes.size() : body.size();
        std::string head = "HTTP/1.1 " + std::to_string(resp.status) +
            (resp.status == 200 ? " OK\r\n" : " Error\r\n") +
            "Content-Type: " + (binary ? resp.mime : "application/json") +
            "\r\nContent-Length: " + std::to_string(size) + "\r\nConnection: close\r\n";
        for (const auto& [name, value] : resp.headers) {
            std::string lower = name;
            for (auto& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            const bool token = !name.empty() && std::all_of(name.begin(), name.end(), [](unsigned char c) {
                return std::isalnum(c) || std::string_view{"!#$%&'*+-.^_`|~"}.find(c) != std::string_view::npos;
            });
            if (!token || !validValue(value) || lower == "content-length" ||
                lower == "content-type" || lower == "connection" || lower == "transfer-encoding") continue;
            head += name + ": " + value + "\r\n";
        }
        head += "\r\n";
        const auto deadline = std::chrono::steady_clock::now() + kIoDeadline;
        if (SendAll(client, head.data(), head.size(), deadline))
            SendAll(client, binary ? reinterpret_cast<const char*>(resp.bytes.data()) : body.data(), size, deadline);
    }

    void ServeInner(SOCKET client)
    {
        Http::Request req;
        if (!ReadRequest(client, req)) {
            SendResponse(client, Http::Response::Error(400, "malformed request"));
            return;
        }

        const auto it = g_routes.find(req.method + " " + req.path);
        if (it == g_routes.end()) {
            SendResponse(client, Http::Response::Error(404, "no such route: " + req.method + " " + req.path));
            return;
        }

        // A handler that throws must not kill the accept thread — that would
        // silently take the whole bridge offline mid-run.
        try {
            SendResponse(client, it->second(req));
        } catch (const std::exception& e) {
            SKSE::log::error("AgentBridge: handler for {} {} threw: {}", req.method, req.path, e.what());
            SendResponse(client, Http::Response::Error(500, e.what()));
        } catch (...) {
            SKSE::log::error("AgentBridge: handler for {} {} threw (unknown)", req.method, req.path);
            SendResponse(client, Http::Response::Error(500, "unknown exception"));
        }
    }

    // Nothing may leave this frame. Serve() runs on a bare std::thread, so an
    // escaping exception is std::terminate — the whole game process, not just
    // the bridge. The handler try/catch inside ServeInner is not enough: the
    // 400 and 404 replies happen before it, and SendResponse itself can throw
    // (json::dump on a request path that is not valid UTF-8 raises type_error
    // 316, and `curl 127.0.0.1:5099/caf%E9` is enough to reach it). The catch
    // in ServeInner can throw for the same reason while reporting a failure.
    void Serve(SOCKET client)
    {
        try {
            ServeInner(client);
        } catch (const std::exception& e) {
            SKSE::log::error("AgentBridge: request failed outside the handler: {}", e.what());
        } catch (...) {
            SKSE::log::error("AgentBridge: request failed outside the handler (unknown)");
        }
    }

    void AcceptLoop()
    {
        while (g_running.load()) {
            const SOCKET listener = g_listen.load();
            if (listener == INVALID_SOCKET) break;

            // select() rather than a blocking accept() so Stop() doesn't have to
            // wait for a connection that may never come.
            fd_set fds;
            FD_ZERO(&fds);
            FD_SET(listener, &fds);
            timeval tv{ 0, kAcceptPollMs * 1000 };
            const int ready = ::select(static_cast<int>(listener + 1), &fds, nullptr, nullptr, &tv);
            if (ready <= 0) continue;

            const SOCKET client = ::accept(listener, nullptr, nullptr);
            if (client == INVALID_SOCKET) continue;

            if (!SetNonblocking(client)) {
                SKSE::log::error("AgentBridge: failed to configure nonblocking client ({})", SocketError());
                ::closesocket(client);
                continue;
            }

            Serve(client);
            ::shutdown(client, SD_SEND);
            ::closesocket(client);
        }
    }
}

std::string Http::Request::Get(const std::string& key) const
{
    const auto it = query.find(key);
    return it == query.end() ? std::string{} : it->second;
}

void Http::Route(std::string method, std::string path, Handler handler)
{
    g_routes[method + " " + path] = std::move(handler);
}

bool Http::Start(std::uint16_t port)
{
    if (g_running.load()) return true;

    #ifndef AGENT_BRIDGE_HTTP_HOST_TEST
    WSADATA wsa{};
    if (const int rc = ::WSAStartup(MAKEWORD(2, 2), &wsa); rc != 0) {
        SKSE::log::error("AgentBridge: WSAStartup failed ({})", rc);
        return false;
    }
    g_wsaStarted = true;
    #endif

    const SOCKET listener = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET) {
        SKSE::log::error("AgentBridge: socket() failed ({})", SocketError());
        CleanupSockets();
        g_wsaStarted = false;
        return false;
    }

    BOOL yes = TRUE;
    ::setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&yes), sizeof(yes));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = ::htons(port);
    // Loopback only, never INADDR_ANY: this thing executes console commands.
    // It must not be reachable from the network, ever.
    addr.sin_addr.s_addr = ::htonl(INADDR_LOOPBACK);

    if (::bind(listener, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
        SKSE::log::error("AgentBridge: bind(127.0.0.1:{}) failed ({}) — port in use?",
            port, SocketError());
        ::closesocket(listener);
        CleanupSockets();
        g_wsaStarted = false;
        return false;
    }
    if (::listen(listener, 8) == SOCKET_ERROR) {
        SKSE::log::error("AgentBridge: listen failed ({})", SocketError());
        ::closesocket(listener);
        CleanupSockets();
        g_wsaStarted = false;
        return false;
    }

    g_listen.store(listener);
    g_running.store(true);
    g_thread = std::thread(AcceptLoop);

    SKSE::log::info("AgentBridge: listening on 127.0.0.1:{} ({} route(s))", port, g_routes.size());
    return true;
}

void Http::OnStop(std::function<void()> handler)
{
    g_onStop = std::move(handler);
}

void Http::Stop()
{
    if (!g_running.exchange(false)) return;
    try {
        if (g_onStop) g_onStop();
    } catch (...) {
        SKSE::log::error("AgentBridge: Stop callback threw");
    }

    if (const SOCKET listener = g_listen.exchange(INVALID_SOCKET); listener != INVALID_SOCKET) {
        ::closesocket(listener);
    }
    if (g_thread.joinable()) g_thread.join();
    if (g_wsaStarted) {
        CleanupSockets();
        g_wsaStarted = false;
    }
    SKSE::log::info("AgentBridge: stopped");
}

bool Http::IsRunning()
{
    return g_running.load();
}

#ifdef AGENT_BRIDGE_HTTP_HOST_TEST
// Deterministic faults exercise the same retry/cancellation code as Winsock.
namespace Http::Test {
    void RetryReceive() { g_retryReceive = 1; }
    void RetrySend() { g_retrySend = 1; }
    void FailNonblocking() { g_failNonblocking = true; }
}
#endif
