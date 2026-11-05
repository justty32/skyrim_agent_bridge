#include "PCH.h"
#include "Screenshot.h"
#include "ScreenshotHookCore.h"
#include <windows.h>
#include <cstring>

// Primary render-phase/callsite evidence (RenderUIHook::install/thunk1/thunk2):
// https://github.com/QTR-Modding/SKSE-Menu-Framework-3/blob/59a14ce192d63022e9174adf3da8c317b5f3091e/src/Hooks.cpp
// This independent hook chains each existing E8 target and preserves its result.
// No swapchain/vtable replacement or framework runtime dependency is required.
namespace Screenshot {
void OnRenderFrame() noexcept;
void StartService();
namespace {
using Hook::Render;
Render previous1{}, previous2{};
bool installed = false;
std::int64_t Render1(std::int64_t manager) {
    return Hook::Invoke(previous1, manager, OnRenderFrame);
}
std::int64_t Render2(std::int64_t manager) {
    return Hook::Invoke(previous2, manager, OnRenderFrame);
}
bool Executable(std::uintptr_t address) {
    MEMORY_BASIC_INFORMATION memory{};
    if (!VirtualQuery(reinterpret_cast<void*>(address), &memory, sizeof(memory)) || memory.State != MEM_COMMIT ||
        (memory.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
    const auto mode = memory.Protect & 0xff;
    return mode == PAGE_EXECUTE || mode == PAGE_EXECUTE_READ || mode == PAGE_EXECUTE_READWRITE || mode == PAGE_EXECUTE_WRITECOPY;
}
bool ValidCall(std::uintptr_t address) {
    if (!Executable(address) || !Executable(address + 4) || *reinterpret_cast<const std::uint8_t*>(address) != 0xe8) return false;
    const auto target = Hook::CallTarget(address, {reinterpret_cast<const std::uint8_t*>(address), 5});
    return target && Executable(*target);
}
}
bool Install() {
    // Must run during SKSEPlugin_Load, before the game's render threads start.
    // Stop keeps the hook resident and pass-through for the DLL's process lifetime.
    if (installed) { StartService(); return true; }
    if (REL::Module::IsVR()) return false;
    const auto version = REL::Module::get().version();
    // Fail closed for unverified layouts (including future 1.7 offsets).
    if (version != SKSE::RUNTIME_SSE_1_5_97 && version != SKSE::RUNTIME_SSE_1_6_1170) return false;
    const auto first = REL::RelocationID(35556, 36555).address() + REL::Relocate(0x3ab, 0x371);
    const auto second = REL::RelocationID(38085, 39039).address() + 0x19a;
    if (!ValidCall(first) || !ValidCall(second)) return false;
    SKSE::AllocTrampoline(28);
    auto& trampoline = SKSE::GetTrampoline();
    previous1 = reinterpret_cast<Render>(trampoline.write_call<5>(first, Render1));
    previous2 = reinterpret_cast<Render>(trampoline.write_call<5>(second, Render2));
    installed = true;
    StartService();
    return true;
}
}
