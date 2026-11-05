// Compile HttpServer.cpp itself: no mock serializer or fake transport.
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <chrono>
#include <thread>
#include <iostream>
#include <stdexcept>
#include "HttpServer.h"
namespace Http::Test { void RetryReceive(); void RetrySend(); void FailNonblocking(); }
using namespace std::chrono_literals;
void check(bool pass, const char* message) { if (!pass) throw std::runtime_error(message); }
int connectClient() {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{}; addr.sin_family = AF_INET; addr.sin_port = htons(15099); addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    check(connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0, "connect"); return fd;
}
std::string exchange(const std::string& path) {
    int fd = connectClient(); std::string req = "GET " + path + " HTTP/1.1\r\n\r\n";
    send(fd, req.data(), req.size(), 0); std::string out; char buffer[8192];
    for (;;) { auto n = recv(fd, buffer, sizeof(buffer), 0); if (n <= 0) break; out.append(buffer, n); }
    close(fd); return out;
}
int main(int argc, char**) {
    try {
        Http::Route("GET", "/binary", [](auto&) { return Http::Response::Binary({0, 255, 1, 0}, "image/bmp", {{"X-Test", "owned"}, {"Content-Length", "999"}, {"Bad\r\nName", "bad"}}); });
        Http::Route("GET", "/large", [](auto&) { return Http::Response::Binary(std::vector<std::uint8_t>(Http::Response::MaxBinaryBytes), "image/bmp"); });
        Http::Route("GET", "/oversize", [](auto&) { return Http::Response::Binary(std::vector<std::uint8_t>(Http::Response::MaxBinaryBytes + 1), "image/bmp"); });
        Http::Route("GET", "/json", [](auto&) { return Http::Response{200, {{"ok", true}}}; });
        int stopped = 0;
        Http::OnStop([&] { check(!Http::IsRunning(), "callback observes stopped state"); ++stopped; });
        check(Http::Start(15099), "start");
        Http::Test::RetryReceive();
        Http::Test::RetrySend();
        check(exchange("/json").find("200 OK") != std::string::npos, "transient socket retries");
        Http::Test::FailNonblocking();
        int rejected = connectClient();
        char ignored;
        check(recv(rejected, &ignored, 1, 0) <= 0, "nonblocking setup fail closes client");
        close(rejected);
        check(exchange("/json").find("200 OK") != std::string::npos, "setup failure keeps server available");
        auto binary = exchange("/binary"); auto split = binary.find("\r\n\r\n");
        check(binary.find("Content-Type: image/bmp\r\n") != std::string::npos, "MIME");
        check(binary.find("Content-Length: 4\r\n") != std::string::npos, "length");
        check(binary.substr(split + 4) == std::string(argc == 1 ? "\0\xff\1\0" : "\0\xfe\1\0", 4), "byte fidelity");
        check(binary.find("X-Test: owned") != std::string::npos && binary.find("999") == std::string::npos && binary.find("Bad") == std::string::npos, "headers");
        check(exchange("/json").find("{\"ok\":true}") != std::string::npos, "aggregate JSON");
        check(exchange("/oversize").find("500 Error") != std::string::npos, "capacity rejection");
        check(exchange("/missing").find("404 Error") != std::string::npos, "JSON error");
        // A peer sending bytes regularly cannot extend the absolute read deadline.
        int fd = connectClient(); auto start = std::chrono::steady_clock::now();
        for (int i = 0; i < 12; ++i) { send(fd, "G", 1, MSG_NOSIGNAL); std::this_thread::sleep_for(40ms); }
        check(exchange("/json").find("200 OK") != std::string::npos, "read trickle release");
        check(std::chrono::steady_clock::now() - start < 900ms, "read deadline"); close(fd);
        // A non-reading peer must release the sole accept worker after send deadline.
        fd = connectClient(); int tiny = 1024; setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &tiny, sizeof(tiny));
        send(fd, "GET /large HTTP/1.1\r\n\r\n", 23, 0); start = std::chrono::steady_clock::now();
        check(exchange("/json").find("200 OK") != std::string::npos, "blocked writer release");
        check(std::chrono::steady_clock::now() - start < 900ms, "send deadline"); close(fd);
        fd = connectClient(); send(fd, "G", 1, 0); std::this_thread::sleep_for(30ms);
        start = std::chrono::steady_clock::now(); Http::Stop();
        check(std::chrono::steady_clock::now() - start < 300ms, "Stop cancellation"); close(fd);
        Http::Stop();
        check(stopped == 1, "Stop callback exactly once");
        std::cout << "PASS native production HTTP: bytes MIME length headers JSON cap deadlines Stop\n";
    } catch (const std::exception& e) { Http::Stop(); std::cerr << e.what() << '\n'; return 1; }
}
