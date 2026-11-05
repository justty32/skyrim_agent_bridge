#include "ScreenshotCore.h"
#include <algorithm>
#include <exception>
#include <limits>

namespace Screenshot {
namespace {
void Put(std::vector<std::uint8_t>& bytes, std::size_t at, std::uint32_t value, unsigned n = 4) {
    for (unsigned i = 0; i < n; ++i) bytes[at + i] = std::uint8_t(value >> (8 * i));
}
Result Failure(std::string error) { Result result; result.error = std::move(error); return result; }
}
Result Encode(std::span<const std::uint8_t> pixels, std::uint32_t width,
    std::uint32_t height, std::size_t rowPitch, PixelFormat format) {
    if (!width || !height || width > MaxWidth || height > MaxHeight) return Failure("unsupported_dimensions");
    if (format == PixelFormat::unsupported) return Failure("unsupported_format");
    const std::size_t rowBytes = std::size_t(width) * 4;
    if (rowPitch < rowBytes || rowPitch > std::numeric_limits<std::size_t>::max() / height ||
        pixels.size() < (std::size_t(height) - 1) * rowPitch + rowBytes) return Failure("invalid_row_pitch");
    Result result;
    result.width = width; result.height = height;
    result.bytes.resize(54 + rowBytes * height);
    auto& bytes = result.bytes;
    bytes[0] = 'B'; bytes[1] = 'M';
    Put(bytes, 2, std::uint32_t(bytes.size())); Put(bytes, 10, 54); Put(bytes, 14, 40);
    Put(bytes, 18, width); Put(bytes, 22, 0u - height); Put(bytes, 26, 1, 2); Put(bytes, 28, 32, 2);
    Put(bytes, 34, std::uint32_t(rowBytes * height));
    for (std::size_t y = 0; y < height; ++y) {
        auto* dst = bytes.data() + 54 + rowBytes * y;
        const auto* src = pixels.data() + rowPitch * y;
        for (std::size_t x = 0; x < rowBytes; x += 4) {
            dst[x] = src[x + (format == PixelFormat::rgba ? 2 : 0)];
            dst[x + 1] = src[x + 1];
            dst[x + 2] = src[x + (format == PixelFormat::rgba ? 0 : 2)];
            dst[x + 3] = 255; // BI_RGB has no alpha semantics.
        }
    }
    return result;
}
void Service::Start() { std::lock_guard lock(mutex_); running_ = true; }
void Service::Finish(const std::shared_ptr<Request>& request, Result result) {
    result.requestId = request->id;
    request->result = std::move(result); request->done = true;
    if (pending_ == request) pending_.reset();
    changed_.notify_all();
}
void Service::Cancel(std::string reason) {
    std::lock_guard lock(mutex_);
    if (pending_) { auto request = pending_; Finish(request, Failure(std::move(reason))); }
}
void Service::Stop() {
    std::lock_guard lock(mutex_); running_ = false;
    if (pending_) { auto request = pending_; Finish(request, Failure("stopped")); }
}
Result Service::Capture(std::chrono::milliseconds timeout) {
    std::unique_lock lock(mutex_);
    if (!running_) return Failure("unavailable");
    if (pending_) return Failure("busy");
    timeout = std::min(timeout, std::chrono::milliseconds(2000));
    auto request = std::make_shared<Request>();
    request->id = ++nextId_; request->deadline = std::chrono::steady_clock::now() + timeout;
    pending_ = request;
    if (!changed_.wait_until(lock, request->deadline, [&] { return request->done; })) {
        Finish(request, Failure("timeout"));
    }
    return std::move(request->result);
}
bool Service::HasPending() { std::lock_guard lock(mutex_); return bool(pending_); }
void Service::OnFrame(Readback& backend) {
    std::unique_lock frameLock(frameMutex_, std::try_to_lock);
    if (!frameLock.owns_lock()) return; // A reentrant callback never touches immediate context.
    std::shared_ptr<Request> request;
    std::uint64_t frame;
    {
        std::unique_lock lock(mutex_, std::try_to_lock);
        if (!lock.owns_lock()) return;
        frame = ++frameId_; request = pending_;
    }
    if (!request) { backend.Reset(); return; }
    ReadResult read;
    try { read = backend.Poll(request->id, frame); }
    catch (const std::exception&) { read.pending = false; read.result = Failure("readback_exception"); }
    catch (...) { read.pending = false; read.result = Failure("readback_exception"); }
    bool reset = false;
    {
        std::unique_lock lock(mutex_, std::try_to_lock);
        if (!lock.owns_lock()) return; // Retain staging and retry completion next callback.
        if (pending_ != request || request->done) reset = true;
        else if (std::chrono::steady_clock::now() >= request->deadline) {
            Finish(request, Failure("timeout")); reset = true;
        } else if (!read.pending) {
            // Backend identifies the copied frame, which may precede this Map frame.
            Finish(request, std::move(read.result)); reset = true;
        }
    }
    if (reset) backend.Reset();
}
}
