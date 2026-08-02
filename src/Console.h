#pragma once

#include <string>
#include <string_view>
#include <vector>

// Running console commands from the bridge, and reading back what they printed.
//
// Output capture reads `ConsoleLog::lastMessage` — the game's own "last thing
// printed" buffer — and returns it only if it changed across the command. That
// means **one line at most**. Multi-line output (`sqs`, `help`) comes back
// truncated to its final line.
//
// This is a deliberate downgrade from the obvious design. See README
// "Pitfall: do not hook ConsoleLog::VPrint" — the detour that would capture
// every line crashed the game on 2026-08-02, because this load order already
// has two plugins hooking console output.
namespace Console {
    struct Result {
        bool ran = false;
        std::vector<std::string> output;   // 0 or 1 entries; see above
    };

    // MUST be called on the game thread (see GameThread::Run). Creating a Script
    // form and compiling it off-thread is a crash waiting to happen.
    Result Execute(std::string_view command, RE::TESObjectREFR* target);
}
