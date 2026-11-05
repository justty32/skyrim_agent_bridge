#pragma once
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

namespace Input::Core {
using Clock = std::chrono::steady_clock;
struct Result {
    std::uint64_t requestId{0};
    std::uint32_t scanCode{0};
    std::string status{"rejected"};
    std::string reason;
    bool downQueued{false};
    bool upQueued{false};
};
struct Environment {
    bool allowed{false};
    bool mapped{false};
    bool physicalDown{false};
    bool capacity{false};
};
// Callbacks run only in the native keyboard phase, under the service mutex.
// They must not reenter Service. Emit returns true only after queue insertion.
using Probe = std::function<Environment(std::uint32_t)>;
using Emit = std::function<bool(std::uint32_t, float, float)>;
class Service {
    struct Request;
public:
    using Ticket = std::shared_ptr<Request>;
    void Start();
    void Stop();
    Ticket Submit(std::uint32_t scanCode,
        std::chrono::milliseconds timeout = std::chrono::seconds(2),
        Clock::time_point now = Clock::now());
    Result Wait(const Ticket& request);
    Result Tap(std::uint32_t scanCode,
        std::chrono::milliseconds timeout = std::chrono::seconds(2));
    Result Snapshot(const Ticket& request);
    void Cancel(const Ticket& request, std::string reason = "cancelled");
    void Cancel(std::string reason = "cancelled");
    bool HasPending();
    // One call per completed keyboard Process invocation. Down, up, and
    // acknowledgement occupy separate phases. The third phase proves the
    // preceding poll returned; completed does not assert a gameplay effect.
    void Tick(Clock::time_point now, const Probe& probe, const Emit& emit);
private:
    struct Request {
        Result result;
        Clock::time_point deadline, downAt;
        bool responseReady{false};
        bool aborted{false};
    };
    void Abort(const Ticket& request, std::string reason,
        std::string beforeDownStatus = "cancelled");
    void Finish(const Ticket& request);
    std::mutex mutex_;
    std::condition_variable changed_;
    bool running_{false};
    std::uint64_t nextId_{0};
    Ticket pending_;
};
}
