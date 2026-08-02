#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <unordered_map>

// Minimal HTTP/1.1 server on 127.0.0.1, living inside the Skyrim process.
//
// Deliberately hand-rolled on winsock rather than pulling in cpp-httplib: the
// whole surface is a handful of localhost JSON routes called by one client, and
// every dependency added here has to survive the clang-cl + xwin cross-compile
// (see README). ~200 lines of socket code is cheaper than that risk.
//
// Not a general-purpose server. It handles one connection at a time, closes
// after each response (Connection: close), and caps the request size.
namespace Http {
    struct Request {
        std::string method;
        std::string path;                                       // no query string
        std::unordered_map<std::string, std::string> query;
        std::string body;

        // Query param or empty string. `/console?cmd=coc+Whiterun` -> Get("cmd").
        std::string Get(const std::string& key) const;
    };

    struct Response {
        int status = 200;
        nlohmann::json body = nlohmann::json::object();

        static Response Ok(nlohmann::json j) { return { 200, std::move(j) }; }
        static Response Error(int status, std::string_view msg) {
            return { status, { { "ok", false }, { "error", std::string{ msg } } } };
        }
    };

    using Handler = std::function<Response(const Request&)>;

    // Register a route. Call before Start(); the table is not locked.
    void Route(std::string method, std::string path, Handler handler);

    // Binds 127.0.0.1:port and spawns the accept thread. False if the port is
    // taken or winsock refuses — the caller logs and carries on, because a
    // bridge that can't listen must not stop the game from loading.
    bool Start(std::uint16_t port);

    void Stop();

    bool IsRunning();
}
