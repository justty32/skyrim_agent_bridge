#include "ScreenshotReadback.h"
namespace Screenshot {
namespace {
ReadResult Fail(std::string error) { ReadResult read; read.pending = false; read.result.error = std::move(error); return read; }
}
void Pipeline::Reset() noexcept { source_.Reset(); request_ = copiedFrame_ = 0; staged_ = {}; }
ReadResult Pipeline::Poll(std::uint64_t request, std::uint64_t frame) {
    if (request_ && request_ != request) Reset();
    // Close releases the current frame's device/context/backbuffer even on exceptions.
    struct Close { SurfaceSource& source; ~Close() { source.Close(); } } close{source_};
    auto surface = source_.Open();
    if (!surface.error.empty()) return Fail(surface.error);
    if (!surface.width || !surface.height || surface.width > MaxWidth || surface.height > MaxHeight) return Fail("unsupported_dimensions");
    if (surface.samples != 1 || surface.arraySize != 1 || surface.mipLevels != 1) return Fail("unsupported_texture");
    if (surface.format == PixelFormat::unsupported) return Fail("unsupported_format");
    if (request_ && (surface.chain != staged_.chain || surface.device != staged_.device || surface.width != staged_.width ||
        surface.height != staged_.height || surface.format != staged_.format)) return Fail("surface_changed");
    if (!request_) {
        if (auto error = source_.Copy(); !error.empty()) return Fail(error);
        staged_ = surface; request_ = request; copiedFrame_ = frame;
        return {};
    }
    auto mapping = source_.Map();
    if (mapping.pending) return {};
    if (!mapping.error.empty()) return Fail(mapping.error);
    struct Unmap { SurfaceSource& source; ~Unmap() { source.Unmap(); } } unmap{source_};
    ReadResult read; read.pending = false;
    read.result = Encode(mapping.pixels, staged_.width, staged_.height, mapping.rowPitch, staged_.format);
    read.result.frameId = copiedFrame_;
    return read;
}
}
