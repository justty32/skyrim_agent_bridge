// Production HTTP transport, input JSON handler, queue service and cache seam.
// The host replaces only the engine's device phase and Input::Tap adapter.
#include "HttpServer.h"
#include "Input.h"
#include "InputDispatch.h"
#include "InputRoute.h"
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <array>
#include <atomic>
#include <chrono>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
namespace {
using Input::Core::Clock;
constexpr std::uint16_t kPort = 15109;
void Check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
struct Button {
    struct Data { float value{}, heldDownSecs{}; } data;
    unsigned device{};
    std::uint32_t idCode{};
    std::string userEvent;
    Button* next{};
    Data& GetRuntimeData() { return data; }
};
struct Queue {
    static constexpr unsigned MAX_BUTTON_EVENTS = 10;
    struct Data { std::array<Button, MAX_BUTTON_EVENTS> buttonEvents; } data;
    unsigned buttonEventCount{};
    Button* head{};
    Button* tail{};
    Data& GetRuntimeData() { return data; }
    void PushOntoInputQueue(Button* event) {
        event->next = nullptr;
        if (tail) tail->next = event; else head = event;
        tail = event;
    }
    void Clear() { buttonEventCount = 0; head = tail = nullptr; }
};
struct Event { std::uint32_t scan; float value, duration; };
struct Host {
    Input::Core::Service service;
    std::atomic<bool> running{true}, tickEnabled{true}, allowed{true}, physicalDown{false}, blockAfterDown{false};
    std::atomic<int> timeoutMs{200};
    Queue queue;
    std::mutex eventsMutex, phaseMutex;
    std::vector<Event> events;
    std::thread thread;
    bool mutate{};
    void Start(bool mutation) {
        mutate = mutation;
        service.Start();
        thread = std::thread([this] {
            while (running.load()) {
                if (tickEnabled.load()) Phase();
                std::this_thread::sleep_for(2ms);
            }
        });
    }
    void Phase() {
        std::lock_guard phaseLock(phaseMutex);
        if (!tickEnabled.load()) return;
        Input::Dispatch::AfterKeyboard([](Host*, float) {}, this, .002f, [this] {
            service.Tick(Clock::now(), [this](std::uint32_t scan) {
                return Input::Core::Environment{allowed.load(), scan != 84,
                    physicalDown.load(), queue.buttonEventCount < Queue::MAX_BUTTON_EVENTS};
            }, [this](std::uint32_t scan, float value, float duration) {
                const auto appended = Input::Dispatch::Append(&queue, 0u, scan, value, duration);
                if (appended && mutate && value == 0) queue.tail->data.value = 1;
                if (appended && value > 0 && blockAfterDown.load()) allowed = false;
                return appended;
            });
        });
        std::lock_guard lock(eventsMutex);
        for (auto* event = queue.head; event; event = event->next) {
            // A fake mapping/dispatch phase observes the production insertion.
            // It does not claim to emulate the game's ControlMap or handlers.
            Check(event >= queue.data.buttonEvents.data() &&
                event < queue.data.buttonEvents.data() + Queue::MAX_BUTTON_EVENTS,
                "event lifetime must remain in cache");
            Check(event->device == 0 && event->userEvent.empty(), "raw keyboard input before mapping");
            events.push_back({event->idCode, event->data.value, event->data.heldDownSecs});
        }
        queue.Clear();
    }
    std::vector<Event> Events() {
        std::lock_guard lock(eventsMutex);
        return events;
    }
    void Pause() {
        tickEnabled = false;
        std::lock_guard lock(phaseMutex);
    }
    void Shutdown() {
        service.Stop();
        running = false;
        if (thread.joinable()) thread.join();
    }
    ~Host() { Shutdown(); }
} host;

struct Reply { int status; nlohmann::json body; };
Reply Exchange(std::string method, std::string path, std::string body = {}) {
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    Check(fd >= 0, "socket");
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(kPort);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        close(fd); throw std::runtime_error("connect");
    }
    timeval timeout{2, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    const std::string request = method + " " + path + " HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: " +
        std::to_string(body.size()) + "\r\n\r\n" + body;
    std::size_t sent = 0;
    while (sent < request.size()) {
        const auto n = send(fd, request.data() + sent, request.size() - sent, MSG_NOSIGNAL);
        if (n <= 0) { close(fd); throw std::runtime_error("send"); }
        sent += static_cast<std::size_t>(n);
    }
    std::string response;
    char buffer[4096];
    for (;;) {
        const auto n = recv(fd, buffer, sizeof(buffer), 0);
        if (n == 0) break;
        if (n < 0) { close(fd); throw std::runtime_error("receive"); }
        response.append(buffer, static_cast<std::size_t>(n));
    }
    close(fd);
    const auto split = response.find("\r\n\r\n");
    Check(split != std::string::npos && response.starts_with("HTTP/1.1 "), "HTTP response framing");
    const auto payload = response.substr(split + 4);
    Check(response.find("Content-Type: application/json\r\n") != std::string::npos, "JSON response MIME");
    Check(response.find("Content-Length: " + std::to_string(payload.size()) + "\r\n") != std::string::npos,
        "JSON response length");
    return {std::stoi(response.substr(9, 3)), nlohmann::json::parse(payload)};
}
Reply Tap(std::uint32_t scan = 42) {
    return Exchange("POST", "/input", nlohmann::json{{"schema", 1}, {"action", "tap"}, {"scan_code", scan}}.dump());
}
void CheckResult(const Reply& reply, int status, std::string_view state, std::string_view reason) {
    Check(reply.status == status, "input HTTP status");
    Check(reply.body.at("schema") == 1 && reply.body.at("request_id").is_number_unsigned(), "result schema identity");
    Check(reply.body.at("state") == state && reply.body.at("reason") == reason, "result state/reason");
}
void WaitDrained() {
    const auto deadline = Clock::now() + 200ms;
    while (host.service.HasPending() && Clock::now() < deadline) std::this_thread::sleep_for(1ms);
    Check(!host.service.HasPending(), "release debt must drain in later phase");
}
void SchemaGuards() {
    const std::vector<std::string> invalid{
        "", "{", "[]", "null", "{}",
        R"({"schema":1,"action":"tap","scan_code":42,"extra":true})",
        R"({"schema":true,"action":"tap","scan_code":42})",
        R"({"schema":2,"action":"tap","scan_code":42})",
        R"({"schema":1,"action":"hold","scan_code":42})",
        R"({"schema":1,"action":"tap","scan_code":true})",
        R"({"schema":1,"action":"tap","scan_code":42.5})",
        R"({"schema":1,"action":"tap","scan_code":"42"})",
        R"({"schema":1,"action":"tap","scan_code":0})",
        R"({"schema":1,"action":"tap","scan_code":-1})",
        R"({"schema":1,"action":"tap","scan_code":255})",
        R"({"schema":1,"action":"tap","scan_code":18446744073709551615})"
    };
    const auto before = host.Events().size();
    for (const auto& body : invalid) {
        const auto reply = Exchange("POST", "/input", body);
        Check(reply.status == 400 && reply.body.at("ok") == false, "strict invalid input JSON guard");
    }
    Check(Exchange("POST", "/input?scan_code=42", R"({"schema":1,"action":"tap","scan_code":42})").status == 400,
        "query parameters rejected");
    Check(Exchange("GET", "/input").status == 404, "input POST only");
    Check(host.Events().size() == before, "invalid wire requests emit no native events");
}
}

// This is the production adapter's only engine-free replacement. HTTP handler
// and Service::Tap remain the exact production implementations under test.
namespace Input {
Core::Result Tap(std::uint32_t scanCode) { return host.service.Tap(scanCode, std::chrono::milliseconds(host.timeoutMs.load())); }
}

int main(int argc, char**) {
    try {
        host.Start(argc > 1);
        Http::Route("POST", "/input", InputRoute::Handle);
        // Transport smoke only: production capability wiring lives in Routes.cpp.
        Http::Route("GET", "/ping", [](const auto&) {
            return Http::Response::Ok({{"ok", true}, {"capabilities", {{"input", nlohmann::json::array({1})}}}});
        });
        Http::OnStop([] { host.service.Stop(); });
        Check(Http::Start(kPort), "start input HTTP host");
        Check(Exchange("GET", "/ping").body.at("capabilities").at("input") == nlohmann::json::array({1}),
            "capability JSON transport smoke");
        SchemaGuards();
        const auto success = Tap();
        CheckResult(success, 200, "completed", "");
        Check(success.body.at("down_dispatched") == true && success.body.at("up_dispatched") == true,
            "completed response requires native pair");
        const auto events = host.Events();
        Check(events.size() == 2 && events[0].scan == 42 && events[1].scan == 42 &&
            events[0].value == 1 && events[0].duration == 0 &&
            events[1].value == 0 && events[1].duration > 0, "production down/up dispatch oracle");
        CheckResult(Tap(84), 400, "rejected", "invalid_key");
        host.physicalDown = true;
        CheckResult(Tap(), 400, "rejected", "physical_key_down");
        host.physicalDown = false;
        for (const auto* boundary : {"menu", "focus", "loading"}) {
            (void)boundary;
            host.allowed = false;
            CheckResult(Tap(), 503, "cancelled", "environment_blocked");
            host.allowed = true;
        }
        Check(host.Events().size() == 2, "runtime guards prevent down");
        host.blockAfterDown = true;
        const auto interrupted = Tap();
        CheckResult(interrupted, 503, "unknown", "environment_blocked");
        Check(interrupted.body.at("down_dispatched") == true && interrupted.body.at("up_dispatched") == true,
            "environment change after down still dispatches release");
        WaitDrained();
        const auto released = host.Events();
        Check(released.size() == 4 && released[2].value == 1 && released[3].value == 0 && released[3].duration > 0,
            "cancelled wire operation retains exactly one native release");
        host.blockAfterDown = false;
        host.allowed = true;
        host.Pause();
        auto pending = host.service.Submit(42, 500ms);
        CheckResult(Tap(), 409, "rejected", "busy");
        host.service.Cancel(pending);
        host.timeoutMs = 35;
        const auto start = Clock::now();
        CheckResult(Tap(), 503, "cancelled", "deadline");
        Check(Clock::now() - start < 500ms && !host.service.HasPending(), "no-phase deadline releases slot");
        host.tickEnabled = true;
        host.timeoutMs = 200;
        CheckResult(Tap(43), 200, "completed", "");
        Http::Stop();
        host.Shutdown();
        std::cout << "PASS production input HTTP: strict JSON, down/up, invalid, busy, environment, deadline, Stop\n";
        return 0;
    } catch (const std::exception& error) {
        Http::Stop(); host.Shutdown();
        std::cerr << "input HTTP: " << error.what() << '\n';
        return 1;
    }
}
