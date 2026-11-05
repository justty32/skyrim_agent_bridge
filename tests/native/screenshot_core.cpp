#include "ScreenshotCore.h"
#include <atomic>
#include <cassert>
#include <future>
#include <iostream>
#include <thread>
using namespace Screenshot;
using namespace std::chrono_literals;
namespace {
void Check(bool condition) { if (!condition) throw std::runtime_error("screenshot host check failed"); }
struct Fake final : Readback {
    std::atomic<unsigned> polls{0}, resets{0};
    bool pending{false}; std::string error; std::uint64_t lastRequest{0};
    ReadResult Poll(std::uint64_t request, std::uint64_t frame) override {
        ++polls; lastRequest = request;
        ReadResult read; read.pending = pending;
        if (!pending) { read.result = Encode(std::vector<std::uint8_t>{255,0,0,0}, 1,1,4,PixelFormat::rgba); read.result.error = error; read.result.frameId = frame; }
        return read;
    }
    void Reset() noexcept override { ++resets; }
};
void WaitPending(Service& service) {
    const auto deadline = std::chrono::steady_clock::now() + 1s;
    while (!service.HasPending() && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    Check(service.HasPending());
}
struct Blocking final : Readback {
    std::promise<void> entered, release;
    unsigned resets{0};
    ReadResult Poll(std::uint64_t, std::uint64_t frame) override {
        entered.set_value(); release.get_future().wait();
        ReadResult read; read.pending=false;
        read.result=Encode(std::vector<std::uint8_t>{1,2,3,4},1,1,4,PixelFormat::rgba);
        read.result.frameId=frame; return read;
    }
    void Reset() noexcept override { ++resets; }
};
std::future<Result> Launch(Service& service, std::chrono::milliseconds timeout = 500ms) {
    return std::async(std::launch::async, [&service, timeout] { return service.Capture(timeout); });
}
// Wait until the actual request is registered, without exposing implementation.
void RenderUntil(Service& service, Fake& fake, std::future<Result>& future) {
    const auto deadline = std::chrono::steady_clock::now() + 1s;
    while (future.wait_for(0ms) != std::future_status::ready && std::chrono::steady_clock::now() < deadline) {
        service.OnFrame(fake); std::this_thread::yield();
    }
    Check(future.wait_for(0ms) == std::future_status::ready);
}
}
int main(int argc, char**) {
    try {
        // An intentional corruption switches the same pixel oracle to red.
        const bool corrupt = argc > 1;
        const std::vector<std::uint8_t> pixels{255,0,0,9, 0,255,0,7, 88,88,88,88,
            0,0,255,6, 255,255,255,1};
        auto image = Encode(pixels, 2,2,12,PixelFormat::rgba);
        Check(image.error.empty() && image.bytes.size() == 70 && image.bytes[22] == 254 && image.bytes[25] == 255);
        const std::vector<std::uint8_t> expected{0,0,255,255, 0,255,0,255, 255,0,0,255, 255,255,255,255};
        if (corrupt) image.bytes[54] ^= 1;
        Check(std::vector<std::uint8_t>(image.bytes.begin()+54,image.bytes.end()) == expected);
        auto bgra = Encode(std::vector<std::uint8_t>{3,2,1,0},1,1,4,PixelFormat::bgra);
        Check(bgra.bytes[54]==3 && bgra.bytes[56]==1 && bgra.bytes[57]==255);
        Check(!Encode(pixels,0,2,12,PixelFormat::rgba).error.empty());
        Check(!Encode(pixels,3841,2,12,PixelFormat::rgba).error.empty());
        Check(!Encode(pixels,2,2161,12,PixelFormat::rgba).error.empty());
        Check(!Encode(pixels,0xffffffff,0xffffffff,12,PixelFormat::rgba).error.empty());
        Check(!Encode(pixels,2,2,7,PixelFormat::rgba).error.empty());
        Check(!Encode(pixels,2,2,SIZE_MAX,PixelFormat::rgba).error.empty());
        Check(!Encode(std::span(pixels).first(19),2,2,12,PixelFormat::rgba).error.empty());
        Check(!Encode(pixels,2,2,12,PixelFormat::unsupported).error.empty());
        Service service; Fake fake;
        Check(service.Capture(1ms).error == "unavailable"); service.Start();
        auto good = Launch(service); RenderUntil(service,fake,good);
        auto result = good.get(); Check(result.error.empty() && result.requestId && result.frameId && result.bytes[56]==255);
        fake.pending = true; auto busy = Launch(service);
        WaitPending(service);
        Check(service.Capture(1ms).error == "busy");
        service.Cancel("cancelled"); Check(busy.get().error == "cancelled");
        auto timeout = Launch(service, 20ms); Check(timeout.get().error == "timeout");
        service.OnFrame(fake); Check(fake.resets > 0);
        fake.pending = false; fake.error = "map_failed";
        auto failed = Launch(service); RenderUntil(service,fake,failed); Check(failed.get().error == "map_failed");
        fake.error = "surface_changed";
        auto resize = Launch(service); RenderUntil(service,fake,resize); Check(resize.get().error == "surface_changed");
        fake.pending = true; auto stopped = Launch(service);
        // Register/observe pending before Stop, avoiding a scheduler race.
        WaitPending(service);
        service.Stop(); Check(stopped.get().error == "stopped");
        service.Start(); fake.pending = false; fake.error.clear();
        auto recovery = Launch(service); RenderUntil(service,fake,recovery); Check(recovery.get().error.empty());
        // A late GPU/encoding completion must never replace a timed-out request.
        Blocking blocking;
        auto stale = Launch(service,20ms); WaitPending(service);
        auto render = std::async(std::launch::async,[&] { service.OnFrame(blocking); });
        blocking.entered.get_future().wait();
        Check(stale.get().error=="timeout");
        auto fresh = Launch(service); WaitPending(service);
        blocking.release.set_value(); render.get(); Check(blocking.resets==1);
        RenderUntil(service,fake,fresh);
        auto freshResult=fresh.get(); Check(freshResult.error.empty() && freshResult.bytes[56]==255);
        std::cout << "screenshot host pixels, bounds, lifecycle, errors, stale completion: PASS\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
