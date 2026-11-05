#include "PCH.h"
#include "ScreenshotReadback.h"
#include <d3d11.h>
#include <wrl/client.h>
#include <limits>

// Public D3D11 staging/CopyResource/Map flow is also used by Microsoft's
// MIT-licensed DirectXTK ScreenGrab.cpp::CaptureTexture; no code copied.
namespace Screenshot {
namespace {
using Microsoft::WRL::ComPtr;
Service service;
class D3DSource final : public SurfaceSource {
    ComPtr<ID3D11Texture2D> staging_, buffer_;
    ComPtr<ID3D11Device> device_, stagingDevice_;
    ComPtr<ID3D11DeviceContext> context_;
    D3D11_TEXTURE2D_DESC desc_{};
public:
    Surface Open() override {
        Surface surface;
        auto fail = [&](const char* error) { surface.error = error; return surface; };
        auto* window = RE::BSGraphics::Renderer::GetCurrentRenderWindow();
        if (!window || !window->swapChain) return fail("no_swapchain");
        if (!window->hWnd || IsIconic(reinterpret_cast<HWND>(window->hWnd))) return fail("minimized");
        auto* chain = reinterpret_cast<IDXGISwapChain*>(window->swapChain);
        if (FAILED(chain->GetDevice(IID_PPV_ARGS(device_.GetAddressOf())))) return fail("device_unavailable");
        if (FAILED(device_->GetDeviceRemovedReason())) return fail("device_removed");
        device_->GetImmediateContext(context_.GetAddressOf());
        if (!context_) return fail("context_unavailable");
        if (FAILED(chain->GetBuffer(0, IID_PPV_ARGS(buffer_.GetAddressOf())))) return fail("buffer_unavailable");
        buffer_->GetDesc(&desc_);
        surface.width = desc_.Width; surface.height = desc_.Height;
        surface.samples = desc_.SampleDesc.Count; surface.arraySize = desc_.ArraySize; surface.mipLevels = desc_.MipLevels;
        switch (desc_.Format) {
        case DXGI_FORMAT_R8G8B8A8_UNORM: case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB: surface.format = PixelFormat::rgba; break;
        case DXGI_FORMAT_B8G8R8A8_UNORM: case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB: surface.format = PixelFormat::bgra; break;
        default: break;
        }
        surface.chain = reinterpret_cast<std::uintptr_t>(chain);
        surface.device = reinterpret_cast<std::uintptr_t>(device_.Get());
        // Pipeline compares the linear/sRGB variants by format family; these
        // cannot share a staging copy, so reject an exact format change too.
        if (staging_) {
            D3D11_TEXTURE2D_DESC old{}; staging_->GetDesc(&old);
            if (old.Format != desc_.Format) return fail("surface_changed");
        }
        return surface;
    }
    std::string Copy() override {
        auto cpu = desc_;
        cpu.Usage = D3D11_USAGE_STAGING; cpu.BindFlags = 0;
        cpu.CPUAccessFlags = D3D11_CPU_ACCESS_READ; cpu.MiscFlags = 0;
        if (FAILED(device_->CreateTexture2D(&cpu, nullptr, staging_.GetAddressOf()))) return "staging_failed";
        stagingDevice_ = device_;
        context_->CopyResource(staging_.Get(), buffer_.Get());
        return {};
    }
    Mapping Map() override {
        buffer_.Reset(); // Never retain a backbuffer during CPU encoding.
        D3D11_MAPPED_SUBRESOURCE mapped{};
        const HRESULT hr = context_->Map(staging_.Get(), 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (hr == DXGI_ERROR_WAS_STILL_DRAWING) return {true, {}, {}, 0};
        if (FAILED(hr)) return {false, "map_failed", {}, 0};
        // Map succeeded: return a view even for invalid stride, so the pipeline
        // always Unmaps after validation rejects it.
        if (!mapped.pData || mapped.RowPitch > std::numeric_limits<std::size_t>::max() / desc_.Height) return {};
        return {false, {}, {static_cast<const std::uint8_t*>(mapped.pData), std::size_t(mapped.RowPitch) * desc_.Height}, mapped.RowPitch};
    }
    void Unmap() noexcept override { context_->Unmap(staging_.Get(), 0); }
    void Close() noexcept override { buffer_.Reset(); context_.Reset(); device_.Reset(); }
    void Reset() noexcept override { staging_.Reset(); stagingDevice_.Reset(); }
};
D3DSource source;
Pipeline readback(source);
}
// Called exclusively at the verified render hook, never from SKSE AddTask.
void OnRenderFrame() noexcept {
    try { service.OnFrame(readback); } catch (...) { service.Cancel("readback_exception"); }
}
void StartService() { service.Start(); }
void Stop() { service.Stop(); }
Result Capture() { return service.Capture(); }
}
