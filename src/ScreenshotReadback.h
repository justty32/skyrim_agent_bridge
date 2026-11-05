#pragma once
#include "ScreenshotCore.h"

namespace Screenshot {
struct Surface {
    std::string error;
    std::uint32_t width{}, height{}, samples{1}, arraySize{1}, mipLevels{1};
    PixelFormat format{PixelFormat::unsupported};
    std::uint64_t chain{}, device{};
};
struct Mapping {
    bool pending{false};
    std::string error;
    std::span<const std::uint8_t> pixels;
    std::size_t rowPitch{};
};
// Only the adapter calls platform APIs. Pipeline owns the actual validation,
// copy/map state machine and resize/device/request cancellation rules.
class SurfaceSource {
public:
    virtual ~SurfaceSource() = default;
    virtual Surface Open() = 0;
    virtual std::string Copy() = 0;
    virtual Mapping Map() = 0;
    virtual void Unmap() noexcept = 0;
    virtual void Close() noexcept = 0;
    virtual void Reset() noexcept = 0;
};
class Pipeline final : public Readback {
public:
    explicit Pipeline(SurfaceSource& source) : source_(source) {}
    ReadResult Poll(std::uint64_t request, std::uint64_t frame) override;
    void Reset() noexcept override;
private:
    SurfaceSource& source_;
    Surface staged_;
    std::uint64_t request_{0}, copiedFrame_{0};
};
}
