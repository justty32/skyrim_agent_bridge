#include "PCH.h"
#include "Input.h"
#include "InputDispatch.h"
#include <atomic>
#include <array>
#include <windows.h>

namespace Input {
namespace {
Core::Service service;
using Process = void (*)(RE::BSWin32KeyboardDevice*, float);
Process previous{};
std::atomic<bool> menuAllowed{false};
std::atomic<HWND> gameWindow{nullptr};
std::atomic<std::uint64_t> menuEpoch{0};
std::atomic<std::uint64_t> snapshotEpoch{~std::uint64_t{0}};
std::atomic<std::int64_t> snapshotAt{0};
std::atomic<bool> refreshQueued{false};
std::array<std::atomic<std::uint64_t>, 4> mappedKeys{};
std::int64_t NowMillis() { return std::chrono::duration_cast<std::chrono::milliseconds>(Core::Clock::now().time_since_epoch()).count(); }
bool installed = false;
std::atomic<bool> started{false};
bool downActive = false; // keyboard-thread only
std::uint64_t downEpoch = 0;

// Menu state is published by the owning event phase, never traversed by the
// keyboard thread. Default closed until kDataLoaded installs this observer.
class MenuObserver final : public RE::BSTEventSink<RE::MenuOpenCloseEvent> {
public:
    void Refresh() {
        const auto epoch = menuEpoch.load(std::memory_order_acquire);
        auto* ui = RE::UI::GetSingleton();
        if (!ui) return;
        RE::BSSpinLockGuard guard(ui->processMessagesLock);
        bool allowed = !ui->GameIsPaused() && !ui->IsApplicationMenuOpen() &&
            !ui->IsItemMenuOpen() && !ui->IsModalMenuOpen() && !ui->closingAllMenus;
        if (ui) {
            for (const auto& menu : ui->menuStack) {
                if (menu && (menu->UsesMenuContext() || menu->UsesCursor() || menu->Modal())) allowed = false;
            }
            if (ui->IsMenuOpen(RE::LoadingMenu::MENU_NAME) || ui->IsMenuOpen(RE::MainMenu::MENU_NAME) ||
                ui->IsMenuOpen(RE::Console::MENU_NAME) || ui->IsMenuOpen(RE::DialogueMenu::MENU_NAME)) allowed = false;
        }
        // ControlMap can be edited by menu rebinding. Resolve it on the game
        // task alongside the UI snapshot; the keyboard thread reads only bits.
        std::array<std::uint64_t, 4> mapped{};
        auto* controls = RE::ControlMap::GetSingleton();
        if (controls) {
            for (std::uint32_t scan = 1; scan < 255; ++scan) {
                if (!controls->GetUserEventName(scan, RE::INPUT_DEVICE::kKeyboard).empty())
                    mapped[scan / 64] |= std::uint64_t{1} << (scan % 64);
            }
        }
        if (menuEpoch.load(std::memory_order_acquire) == epoch) {
            for (std::size_t index = 0; index < mapped.size(); ++index)
                mappedKeys[index].store(mapped[index], std::memory_order_release);
            menuAllowed.store(allowed, std::memory_order_release);
            snapshotAt.store(NowMillis(), std::memory_order_release);
            snapshotEpoch.store(epoch, std::memory_order_release);
        }
    }
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent*,
        RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override {
        menuEpoch.fetch_add(1, std::memory_order_acq_rel);
        menuAllowed.store(false, std::memory_order_release);
        snapshotAt.store(0, std::memory_order_release);
        return RE::BSEventNotifyControl::kContinue;
    }
} observer;

void QueueRefresh() {
    if (!started.load(std::memory_order_acquire) || NowMillis() - snapshotAt.load(std::memory_order_acquire) < 100 ||
        refreshQueued.exchange(true)) return;
    SKSE::GetTaskInterface()->AddTask([] { observer.Refresh(); refreshQueued.store(false); });
}

void Keyboard(RE::BSWin32KeyboardDevice* keyboard, float elapsed) {
    Dispatch::AfterKeyboard(previous, keyboard, elapsed, [keyboard] {
        QueueRefresh();
        std::uint64_t validatedEpoch{};
        service.Tick(Core::Clock::now(), [keyboard, &validatedEpoch](std::uint32_t scan) {
            validatedEpoch = menuEpoch.load(std::memory_order_acquire);
            auto* queue = RE::BSInputEventQueue::GetSingleton();
            const auto window = gameWindow.load(std::memory_order_acquire);
            Core::Environment environment{
                (!downActive || downEpoch == validatedEpoch) &&
                    snapshotEpoch.load(std::memory_order_acquire) == validatedEpoch &&
                    menuAllowed.load(std::memory_order_acquire) && NowMillis() - snapshotAt.load(std::memory_order_acquire) < 250 &&
                    window && GetForegroundWindow() == window,
                scan > 0 && scan < 256 && keyboard->deviceButtons.find(scan) != keyboard->deviceButtons.end() &&
                    (mappedKeys[scan / 64].load(std::memory_order_acquire) & (std::uint64_t{1} << (scan % 64))) != 0,
                scan < 256 && ((keyboard->curState[scan] | keyboard->prevState[scan]) & 0x80) != 0,
                queue && queue->buttonEventCount < RE::BSInputEventQueue::MAX_BUTTON_EVENTS};
            environment.allowed = environment.allowed && validatedEpoch == menuEpoch.load(std::memory_order_acquire);
            return environment;
        }, [&validatedEpoch](std::uint32_t scan, float value, float duration) {
            // Recheck the exact epoch validated by Probe, rather than adopting
            // a new menu epoch between validation and queue insertion.
            const auto window = gameWindow.load(std::memory_order_acquire);
            if (value > 0.0f && (validatedEpoch != menuEpoch.load(std::memory_order_acquire) ||
                !window || GetForegroundWindow() != window)) return false;
            const bool queued = Dispatch::Append(RE::BSInputEventQueue::GetSingleton(), RE::INPUT_DEVICE::kKeyboard,
                scan, value, duration);
            if (queued) {
                downActive = value > 0.0f;
                if (downActive) downEpoch = validatedEpoch;
            }
            return queued;
        });
    });
}
bool Executable(std::uintptr_t address) {
    MEMORY_BASIC_INFORMATION memory{};
    if (!VirtualQuery(reinterpret_cast<void*>(address), &memory, sizeof(memory)) || memory.State != MEM_COMMIT ||
        (memory.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
    const auto mode = memory.Protect & 0xff;
    return mode == PAGE_EXECUTE || mode == PAGE_EXECUTE_READ || mode == PAGE_EXECUTE_READWRITE || mode == PAGE_EXECUTE_WRITECOPY;
}
}
// Installed at SKSEPlugin_Load before the input thread starts. Engine queue
// objects outlive every request. Hook remains chained/pass-through after Stop.
bool Install() {
    if (installed) return true;
    if (REL::Module::IsVR()) return false;
    const auto version = REL::Module::get().version();
    if (version != SKSE::RUNTIME_SSE_1_5_97 && version != SKSE::RUNTIME_SSE_1_6_1170) return false;
    REL::Relocation<std::uintptr_t> table{RE::VTABLE_BSWin32KeyboardDevice[0]};
    const auto target = reinterpret_cast<std::uintptr_t*>(table.address())[2];
    if (!Executable(target)) return false;
    previous = reinterpret_cast<Process>(target);
    table.write_vfunc(2, Keyboard);
    installed = true;
    return true;
}
bool Start() {
    if (!installed) return false;
    auto* main = RE::Main::GetSingleton();
    auto* ui = RE::UI::GetSingleton();
    if (!main || !ui) return false;
    gameWindow.store(reinterpret_cast<HWND>(main->wnd), std::memory_order_release);
    ui->AddEventSink<RE::MenuOpenCloseEvent>(&observer);
    service.Start();
    started.store(true, std::memory_order_release);
    QueueRefresh();
    return true;
}
void Stop() { started.store(false, std::memory_order_release); service.Stop(); }
Core::Result Tap(std::uint32_t scanCode) { return service.Tap(scanCode); }
}
