#pragma once
#include "Screenshot.h"
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <span>

namespace Screenshot {
inline constexpr std::uint32_t MaxWidth = 3840, MaxHeight = 2160;
inline constexpr std::size_t MaxBytes = 54 + std::size_t(MaxWidth) * MaxHeight * 4;
enum class PixelFormat { rgba, bgra, unsupported };
// span covers all bytes including source row padding; no borrowed memory escapes.
Result Encode(std::span<const std::uint8_t> pixels, std::uint32_t width,
    std::uint32_t height, std::size_t rowPitch, PixelFormat format);
struct ReadResult {
    bool pending{true};
    Result result;
};
class Readback {
public:
    virtual ~Readback() = default;
    virtual ReadResult Poll(std::uint64_t requestId, std::uint64_t frameId) = 0;
    virtual void Reset() noexcept = 0;
};
// The HTTP thread only waits on a condition variable. Poll/Reset execute only
// from the render callback, including after cancellation and service shutdown.
class Service {
public:
    void Start();
    void Stop();
    void Cancel(std::string reason);
    Result Capture(std::chrono::milliseconds timeout = std::chrono::seconds(2));
    void OnFrame(Readback& backend);
    bool HasPending();
private:
    struct Request {
        std::uint64_t id;
        std::chrono::steady_clock::time_point deadline;
        bool done{false};
        Result result;
    };
    std::mutex mutex_, frameMutex_;
    std::condition_variable changed_;
    bool running_{false};
    std::uint64_t nextId_{0}, frameId_{0};
    std::shared_ptr<Request> pending_;
    void Finish(const std::shared_ptr<Request>& request, Result result);
};
}
