#include "InputCore.h"
#include "InputDispatch.h"
#include <array>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <vector>

using namespace Input::Core;
using namespace std::chrono_literals;
namespace {
void Check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
struct Button {
    struct Data { float value{}, heldDownSecs{}; } data;
    int device{};
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
        if (tail) tail->next = event;
        else head = event;
        tail = event;
    }
    void Clear() { buttonEventCount = 0; head = tail = nullptr; }
};
struct Event { std::uint32_t scan; float value, duration; };
struct Host {
    Service service;
    Queue queue;
    Environment environment{true, true, false, true};
    std::vector<Event> delivered;
    std::vector<std::string> order;
    Clock::time_point now{Clock::now()};
    bool emitSucceeds{true}, throwEmit{false}, throwProbe{false}, mutate{false};
    Host() { service.Start(); }
    void Phase() {
        now += 10ms;
        // This is the production ordering seam: physical Process first,
        // synthetic insertion second, then the original Poll's map/dispatch.
        Input::Dispatch::AfterKeyboard(
            [this](Host*, float) { order.push_back("physical"); }, this, .01f, [this] {
                order.push_back("synthetic");
                service.Tick(now, [this](std::uint32_t scan) {
                    if (throwProbe) throw std::runtime_error("probe");
                    auto current = environment;
                    current.mapped = current.mapped && scan != 84;
                    current.capacity = current.capacity && queue.buttonEventCount < Queue::MAX_BUTTON_EVENTS;
                    return current;
                }, [this](std::uint32_t scan, float value, float duration) {
                    if (throwEmit) throw std::runtime_error("emit");
                    if (!emitSucceeds) return false;
                    const bool added = Input::Dispatch::Append(&queue, 0, scan, value, duration);
                    if (added && mutate && value == 0) queue.tail->data.value = 1;
                    return added;
                });
            });
        order.push_back("map");
        for (auto* event = queue.head; event; event = event->next) {
            Check(event >= queue.data.buttonEvents.data() &&
                event < queue.data.buttonEvents.data() + Queue::MAX_BUTTON_EVENTS, "event must remain in engine cache");
            Check(event->device == 0 && event->userEvent.empty(), "native ControlMap receives raw keyboard scan");
            delivered.push_back({event->idCode, event->data.value, event->data.heldDownSecs});
        }
        order.push_back("dispatch");
        queue.Clear();
        order.push_back("reset");
    }
};
void CheckResult(Host& host, const Service::Ticket& request, std::string_view status,
    std::string_view reason = {}) {
    const auto result = host.service.Snapshot(request);
    Check(result.status == status, "unexpected request status");
    if (!reason.empty()) Check(result.reason == reason, "unexpected request reason");
}
void Success(bool mutate) {
    Host host; host.mutate = mutate;
    auto request = host.service.Submit(42, 500ms, host.now);
    const auto id = host.service.Snapshot(request).requestId;
    Check(id != 0, "request identity");
    CheckResult(host, request, "accepted");
    auto busy = host.service.Tap(43, 1ms);
    Check(busy.status == "rejected" && busy.reason == "busy", "single pending guard");
    host.Phase();
    Check(host.delivered.size() == 1 && host.delivered[0].scan == 42 &&
        host.delivered[0].value == 1 && host.delivered[0].duration == 0, "first phase must dispatch down");
    CheckResult(host, request, "accepted");
    host.Phase();
    Check(host.delivered.size() == 2 && host.delivered[1].scan == 42 &&
        host.delivered[1].value == 0 && host.delivered[1].duration > 0, "second phase must dispatch up");
    CheckResult(host, request, "accepted");
    Check(host.service.HasPending(), "up requires next phase acknowledgement");
    host.Phase();
    auto result = host.service.Wait(request);
    Check(result.status == "completed" && result.requestId == id &&
        result.downQueued && result.upQueued && result.reason.empty(), "complete native pair acknowledgement");
    Check(host.delivered.size() == 2 && !host.service.HasPending(), "release exactly once");
    const std::vector<std::string> expected{"physical", "synthetic", "map", "dispatch", "reset"};
    Check(std::vector(host.order.begin(), host.order.begin() + 5) == expected, "physical synthetic map dispatch reset order");
    Check(host.queue.buttonEventCount == 0 && !host.queue.head && !host.queue.tail, "cache reset keeps ownership");
}
void Guards() {
    Host host;
    for (const auto scan : {0u, 255u, 256u, 0xffffffffu}) {
        auto result = host.service.Tap(scan, 1ms);
        Check(result.status == "rejected" && result.reason == "invalid_key", "scan bound guard");
    }
    Check(host.service.Tap(42, 0ms).reason == "invalid_timeout", "timeout validation");
    auto missing = host.service.Submit(84, 500ms, host.now);
    host.Phase(); CheckResult(host, missing, "rejected", "invalid_key");
    host.environment.physicalDown = true;
    auto physical = host.service.Submit(42, 500ms, host.now);
    host.Phase(); CheckResult(host, physical, "rejected", "physical_key_down");
    Check(host.delivered.empty(), "rejection must not emit");
    host.environment.physicalDown = false;
    host.environment.allowed = false;
    auto menu = host.service.Submit(42, 500ms, host.now);
    host.Phase(); CheckResult(host, menu, "cancelled", "environment_blocked");
    host.environment.allowed = true;
    auto expired = host.service.Submit(42, 1ms, host.now);
    host.Phase(); CheckResult(host, expired, "cancelled", "deadline");
    Check(host.delivered.empty(), "deadline must prevent down");
    Check(host.service.Tap(42, 1ms).status == "cancelled", "waiter deadline before down");
    host.service.Stop();
    Check(host.service.Tap(42, 1ms).reason == "unavailable", "stopped rejects new requests");
}
void ReleaseDebt() {
    for (unsigned action = 0; action < 5; ++action) {
        Host host;
        auto request = host.service.Submit(42, 500ms, host.now);
        host.Phase();
        if (action == 0) host.service.Cancel(request);
        if (action == 1) host.service.Stop();
        if (action == 2) host.environment.allowed = false; // menu/focus/loading
        if (action == 3) host.now += 1s;
        if (action == 4) host.service.Cancel("load_changed");
        host.Phase();
        CheckResult(host, request, "unknown");
        Check(host.delivered.size() == 2 && host.delivered[1].value == 0,
            "cancel Stop focus deadline must preserve release");
        host.service.Start();
        Check(host.service.Tap(43, 1ms).reason == "busy", "release acknowledgement retains busy slot");
        host.Phase();
        const auto result = host.service.Wait(request);
        Check(result.status == "unknown" && result.downQueued && result.upQueued &&
            !host.service.HasPending(), "unknown never becomes success after cleanup");
    }
    for (bool stop : {false, true}) {
        Host host;
        auto request = host.service.Submit(42, 500ms, host.now);
        if (stop) host.service.Stop(); else host.service.Cancel(request);
        CheckResult(host, request, "cancelled");
        host.Phase();
        Check(host.delivered.empty() && !host.service.HasPending(), "cancel before down emits nothing");
    }
    Host timeout;
    auto request = timeout.service.Submit(42, 1ms, timeout.now + 20ms);
    timeout.Phase(); // deliberately before request's deadline
    const auto result = timeout.service.Wait(request);
    Check(result.status == "unknown" && result.downQueued && !result.upQueued, "waiter timeout after down");
    Check(timeout.service.HasPending(), "wait timeout retains debt");
    timeout.Phase(); timeout.Phase();
    Check(!timeout.service.HasPending() && timeout.delivered.size() == 2, "late phases drain timed-out debt");
}
void CapacityAndFailure() {
    Host host;
    host.environment.capacity = false;
    auto request = host.service.Submit(42, 500ms, host.now);
    host.Phase(); CheckResult(host, request, "accepted");
    Check(host.delivered.empty(), "full queue waits before down");
    host.environment.capacity = true; host.Phase();
    host.environment.capacity = false; host.service.Cancel(request); host.Phase();
    Check(host.service.HasPending() && host.delivered.size() == 1, "full queue preserves release debt");
    host.environment.capacity = true; host.environment.physicalDown = true;
    host.Phase(); host.Phase();
    Check(host.delivered.size() == 2 && host.delivered[1].value == 0, "physical state cannot suppress owed release");
    Host failed;
    auto retry = failed.service.Submit(42, 500ms, failed.now);
    failed.emitSucceeds = false; failed.Phase();
    Check(!failed.service.Snapshot(retry).downQueued, "failed insertion has no down debt");
    failed.emitSucceeds = true; failed.Phase();
    failed.emitSucceeds = false; failed.Phase();
    Check(!failed.service.Snapshot(retry).upQueued && failed.service.HasPending(), "failed up insertion remains owed");
    failed.emitSucceeds = true; failed.Phase(); failed.Phase();
    CheckResult(failed, retry, "completed");
    Queue queue;
    for (unsigned i = 0; i < Queue::MAX_BUTTON_EVENTS; ++i)
        Check(Input::Dispatch::Append(&queue, 0, 42, 1, 0), "cache capacity available");
    auto* tail = queue.tail;
    Check(!Input::Dispatch::Append(&queue, 0, 42, 0, .01f) &&
        queue.buttonEventCount == Queue::MAX_BUTTON_EVENTS && queue.tail == tail,
        "capacity failure must leave queue unchanged");
    Check(!Input::Dispatch::Append(static_cast<Queue*>(nullptr), 0, 42, 1, 0), "null queue guard");
    queue.Clear();
    Check(Input::Dispatch::Append(&queue, 0, 42, 0, .01f) && queue.head == &queue.data.buttonEvents[0],
        "cache reused after reset without allocation");
    Host exception;
    auto thrown = exception.service.Submit(42, 500ms, exception.now);
    exception.throwEmit = true; exception.Phase();
    CheckResult(exception, thrown, "unknown", "dispatch_exception");
    exception.throwEmit = false; exception.Phase(); exception.Phase();
    Check(!exception.service.HasPending(), "uncertain down insertion conservatively releases");
    Host probe;
    auto probeRequest = probe.service.Submit(42, 500ms, probe.now);
    probe.Phase(); probe.throwProbe = true; probe.Phase();
    CheckResult(probe, probeRequest, "unknown", "environment_exception");
    probe.throwProbe = false; probe.Phase(); probe.Phase();
    Check(probe.delivered.size() == 2 && !probe.service.HasPending(), "probe failure preserves release debt");
}
}
int main(int argc, char**) {
    try {
        Success(argc > 1);
        Guards(); ReleaseDebt(); CapacityAndFailure();
        std::cout << "input core native pair, queue seam, guards, deadline, cancellation, Stop, release debt: PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "input core: " << error.what() << '\n';
        return 1;
    }
}
