#include "ScreenshotReadback.h"
#include <iostream>
#include <stdexcept>
using namespace Screenshot;
namespace {
void Check(bool condition) { if (!condition) throw std::runtime_error("readback host check failed"); }
struct Source final : SurfaceSource {
    Surface surface{{}, 2, 2, 1, 1, 1, PixelFormat::rgba, 10, 20};
    std::vector<std::uint8_t> pixels{255,0,0,1, 0,255,0,2, 9,9,9,9,
        0,0,255,3, 255,255,255,4, 8,8,8,8};
    std::size_t pitch{12};
    bool pending{false};
    std::string mapError, copyError;
    unsigned opens{}, closes{}, copies{}, maps{}, unmaps{}, resets{};
    Surface Open() override { ++opens; return surface; }
    std::string Copy() override { ++copies; return copyError; }
    Mapping Map() override { ++maps; return {pending, mapError, pixels, pitch}; }
    void Unmap() noexcept override { ++unmaps; }
    void Close() noexcept override { ++closes; }
    void Reset() noexcept override { ++resets; }
};
}
int main(int argc, char**) {
    try {
        Source source; Pipeline pipeline(source);
        Check(pipeline.Poll(1,101).pending && source.copies==1 && source.maps==0 && source.opens==source.closes);
        source.pending = true;
        Check(pipeline.Poll(1,102).pending && source.copies==1 && source.unmaps==0);
        source.pending = false;
        auto good = pipeline.Poll(1,103);
        if (argc > 1) good.result.frameId = 103; // Same actual-copy-frame oracle must go red.
        Check(!good.pending && good.result.error.empty() && good.result.frameId==101 && source.unmaps==1);
        Check(good.result.bytes[54]==0 && good.result.bytes[56]==255 && good.result.bytes[62]==255);
        pipeline.Reset(); Check(source.resets==1);
        Check(pipeline.Poll(2,104).pending);
        source.surface.width=1;
        Check(pipeline.Poll(2,105).result.error=="surface_changed");
        pipeline.Reset(); source.surface.width=2;
        Check(pipeline.Poll(3,106).pending); ++source.surface.device;
        Check(pipeline.Poll(3,107).result.error=="surface_changed");
        pipeline.Reset(); Check(pipeline.Poll(4,108).pending); ++source.surface.chain;
        Check(pipeline.Poll(4,109).result.error=="surface_changed");
        pipeline.Reset(); Check(pipeline.Poll(5,110).pending);
        source.mapError="map_failed"; const auto unmapped=source.unmaps;
        Check(pipeline.Poll(5,111).result.error=="map_failed" && source.unmaps==unmapped);
        source.mapError.clear(); pipeline.Reset(); Check(pipeline.Poll(6,112).pending);
        source.pitch=7;
        Check(pipeline.Poll(6,113).result.error=="invalid_row_pitch" && source.unmaps==unmapped+1);
        pipeline.Reset(); source.pitch=12; source.copyError="staging_failed";
        Check(pipeline.Poll(7,114).result.error=="staging_failed"); source.copyError.clear();
        pipeline.Reset(); Check(pipeline.Poll(8,115).pending);
        const auto resetBefore=source.resets, copiedBefore=source.copies;
        Check(pipeline.Poll(9,116).pending && source.resets==resetBefore+1 && source.copies==copiedBefore+1);
        auto newRequest=pipeline.Poll(9,117); Check(newRequest.result.frameId==116);
        pipeline.Reset(); source.surface.width=0;
        Check(pipeline.Poll(10,118).result.error=="unsupported_dimensions");
        source.surface.width=2; source.surface.format=PixelFormat::unsupported;
        Check(pipeline.Poll(11,119).result.error=="unsupported_format");
        source.surface.format=PixelFormat::rgba; source.surface.samples=4;
        Check(pipeline.Poll(12,120).result.error=="unsupported_texture");
        source.surface.samples=1; source.surface.error="device_removed";
        Check(pipeline.Poll(13,121).result.error=="device_removed");
        Check(source.opens==source.closes);
        std::cout << "screenshot readback copy/map/resize/device/cancel/stride: PASS\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
