#include "InputCore.h"
#include <algorithm>
#include <cmath>
#include <utility>

namespace Input::Core {
void Service::Start() { std::lock_guard lock(mutex_); running_ = true; }
void Service::Finish(const Ticket& request) {
    request->responseReady = true;
    if (pending_ == request) pending_.reset();
    changed_.notify_all();
}
void Service::Abort(const Ticket& request, std::string reason, std::string beforeDownStatus) {
    if (request->aborted) return;
    request->aborted = true;
    request->result.reason = std::move(reason);
    request->result.status = request->result.downQueued ? "unknown" : std::move(beforeDownStatus);
    request->responseReady = true;
    // Once down was inserted, keep ownership of the sole slot until release
    // has traversed the following native poll, even after the waiter leaves.
    if (!request->result.downQueued && pending_ == request) pending_.reset();
    changed_.notify_all();
}
void Service::Stop() {
    std::lock_guard lock(mutex_);
    running_ = false;
    if (pending_) Abort(pending_, "stopped");
}
Service::Ticket Service::Submit(std::uint32_t scanCode, std::chrono::milliseconds timeout,
    Clock::time_point now) {
    std::lock_guard lock(mutex_);
    auto request = std::make_shared<Request>();
    request->result.requestId = ++nextId_;
    request->result.scanCode = scanCode;
    if (scanCode == 0 || scanCode >= 255) request->result.reason = "invalid_key";
    else if (timeout.count() <= 0) request->result.reason = "invalid_timeout";
    else if (!running_) request->result.reason = "unavailable";
    else if (pending_) request->result.reason = "busy";
    else {
        request->result.status = "accepted";
        request->deadline = now + std::min(timeout, std::chrono::milliseconds(2000));
        pending_ = request;
        return request;
    }
    request->responseReady = true;
    return request;
}
Result Service::Wait(const Ticket& request) {
    std::unique_lock lock(mutex_);
    if (!request) { Result result; result.reason = "invalid_request"; return result; }
    if (!request->responseReady &&
        !changed_.wait_until(lock, request->deadline, [&] { return request->responseReady; })) {
        Abort(request, "deadline");
    }
    return request->result;
}
Result Service::Tap(std::uint32_t scanCode, std::chrono::milliseconds timeout) {
    return Wait(Submit(scanCode, timeout));
}
Result Service::Snapshot(const Ticket& request) {
    std::lock_guard lock(mutex_);
    if (request) return request->result;
    Result result; result.reason = "invalid_request"; return result;
}
void Service::Cancel(const Ticket& request, std::string reason) {
    std::lock_guard lock(mutex_);
    if (request && pending_ == request) Abort(request, std::move(reason));
}
void Service::Cancel(std::string reason) {
    std::lock_guard lock(mutex_);
    if (pending_) Abort(pending_, std::move(reason));
}
bool Service::HasPending() { std::lock_guard lock(mutex_); return bool(pending_); }
void Service::Tick(Clock::time_point now, const Probe& probe, const Emit& emit) {
    std::unique_lock lock(mutex_, std::try_to_lock);
    if (!lock.owns_lock() || !pending_) return;
    const auto request = pending_;
    auto& result = request->result;
    // An up queued during the previous phase has now passed through the
    // original ControlMap/dispatch/reset pipeline before this invocation.
    if (result.upQueued) {
        if (!request->aborted) result.status = "completed";
        Finish(request);
        return;
    }
    if (now >= request->deadline) Abort(request, "deadline");
    if (pending_ != request) return;
    Environment environment;
    try { environment = probe(result.scanCode); }
    catch (...) { Abort(request, "environment_exception"); return; }
    if (!result.downQueued) {
        if (!environment.mapped) { Abort(request, "invalid_key", "rejected"); return; }
        if (!environment.allowed) { Abort(request, "environment_blocked"); return; }
        if (environment.physicalDown) { Abort(request, "physical_key_down", "rejected"); return; }
        if (!environment.capacity) return;
        // Conservatively retain release debt if a callback throws after it
        // may already have inserted the event. The production emitter is noexcept.
        result.downQueued = true;
        request->downAt = now;
        try {
            if (!emit(result.scanCode, 1.0f, 0.0f)) result.downQueued = false;
        } catch (...) { Abort(request, "dispatch_exception"); }
        return;
    }
    if (!environment.allowed) Abort(request, "environment_blocked");
    // Focus/menu changes, cancellation, timeout and Stop never suppress up.
    // Physical key state is untouched: this is an event, not device-state injection.
    if (!environment.capacity) return;
    const auto elapsed = std::chrono::duration<float>(now - request->downAt).count();
    const float duration = std::isfinite(elapsed) ? std::max(elapsed, 0.001f) : 0.001f;
    try { result.upQueued = emit(result.scanCode, 0.0f, duration); }
    catch (...) { Abort(request, "dispatch_exception"); }
}
}
