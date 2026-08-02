#include "HttpServer.h"

#include <atomic>
#include <cctype>
#include <cstdlib>
#include <format>
#include <thread>

namespace {
    constexpr std::size_t kMaxRequestBytes = 1 << 20;   // 1 MiB — /console payloads are tiny
    constexpr int kAcceptPollMs = 200;                  // how often the accept loop checks for Stop()

    std::unordered_map<std::string, Http::Handler> g_routes;    // key: "GET /state"
    std::atomic<bool> g_running{ false };
    std::atomic<SOCKET> g_listen{ INVALID_SOCKET };
    std::thread g_thread;
    bool g_wsaStarted = false;

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
        std::string buf;
        char chunk[4096];
        std::size_t headerEnd = std::string::npos;

        while (headerEnd == std::string::npos) {
            const int n = ::recv(client, chunk, sizeof(chunk), 0);
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
            const auto pos = lower.find("content-length:");
            if (pos != std::string::npos) {
                contentLength = static_cast<std::size_t>(std::strtoul(head.c_str() + pos + 15, nullptr, 10));
                if (contentLength > kMaxRequestBytes) return false;
            }
        }

        req.body = buf.substr(headerEnd + 4);
        while (req.body.size() < contentLength) {
            const int n = ::recv(client, chunk, sizeof(chunk), 0);
            if (n <= 0) return false;
            req.body.append(chunk, static_cast<std::size_t>(n));
        }
        req.body.resize(contentLength);
        return true;
    }

    void SendAll(SOCKET client, const char* data, std::size_t len)
    {
        std::size_t sent = 0;
        while (sent < len) {
            const int n = ::send(client, data + sent, static_cast<int>(len - sent), 0);
            if (n <= 0) return;
            sent += static_cast<std::size_t>(n);
        }
    }

    void SendResponse(SOCKET client, const Http::Response& resp)
    {
        const std::string body = resp.body.dump();
        const std::string head = std::format(
            "HTTP/1.1 {} {}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n",
            resp.status, resp.status == 200 ? "OK" : "Error", body.size());
        SendAll(client, head.data(), head.size());
        SendAll(client, body.data(), body.size());
    }

    void Serve(SOCKET client)
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
            const int ready = ::select(0, &fds, nullptr, nullptr, &tv);
            if (ready <= 0) continue;

            const SOCKET client = ::accept(listener, nullptr, nullptr);
            if (client == INVALID_SOCKET) continue;

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

    WSADATA wsa{};
    if (const int rc = ::WSAStartup(MAKEWORD(2, 2), &wsa); rc != 0) {
        SKSE::log::error("AgentBridge: WSAStartup failed ({})", rc);
        return false;
    }
    g_wsaStarted = true;

    const SOCKET listener = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET) {
        SKSE::log::error("AgentBridge: socket() failed ({})", ::WSAGetLastError());
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
            port, ::WSAGetLastError());
        ::closesocket(listener);
        return false;
    }
    if (::listen(listener, 8) == SOCKET_ERROR) {
        SKSE::log::error("AgentBridge: listen failed ({})", ::WSAGetLastError());
        ::closesocket(listener);
        return false;
    }

    g_listen.store(listener);
    g_running.store(true);
    g_thread = std::thread(AcceptLoop);

    SKSE::log::info("AgentBridge: listening on 127.0.0.1:{} ({} route(s))", port, g_routes.size());
    return true;
}

void Http::Stop()
{
    if (!g_running.exchange(false)) return;

    if (const SOCKET listener = g_listen.exchange(INVALID_SOCKET); listener != INVALID_SOCKET) {
        ::closesocket(listener);
    }
    if (g_thread.joinable()) g_thread.join();
    if (g_wsaStarted) {
        ::WSACleanup();
        g_wsaStarted = false;
    }
    SKSE::log::info("AgentBridge: stopped");
}

bool Http::IsRunning()
{
    return g_running.load();
}
