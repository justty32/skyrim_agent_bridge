#include "Console.h"

#include <cstring>
#include <format>

namespace {
    // Only ever touched on the game thread (Execute's contract), so a plain
    // counter is enough — no atomic needed.
    unsigned g_sentinelSeq = 0;

    // ConsoleLog::lastMessage is a plain char[0x400] the game fills in on every
    // print. Reading it is just a struct member access — no patching, no
    // trampoline, nothing another plugin can collide with.
    const char* LastMessage()
    {
        auto* log = RE::ConsoleLog::GetSingleton();
        return log ? log->lastMessage : nullptr;
    }
}

Console::Result Console::Execute(std::string_view a_command, RE::TESObjectREFR* a_target)
{
    Result result;

    auto* factory = RE::IFormFactory::GetConcreteFormFactoryByType<RE::Script>();
    if (!factory) {
        SKSE::log::error("Console: no Script form factory");
        return result;
    }
    auto* script = factory->Create();
    if (!script) {
        SKSE::log::error("Console: Script::Create returned null");
        return result;
    }

    // Print a sentinel first, then check whether the command overwrote it.
    //
    // The obvious "snapshot before, compare after" does NOT work here, and the
    // 2026-08-02 test run proved it: `load` and `coc` print nothing, yet both
    // came back with lines ("GetInFaction >> 0.00", "IsShieldOut >> 0.00") that
    // some other mod had written to lastMessage in between. Comparing against a
    // sentinel WE wrote turns "nothing was printed" into an empty result instead
    // of a foreign line, because the sentinel is still there if nobody printed.
    //
    // It does not make this airtight — a foreign print landing between the
    // command and our read still leaks through. Callers should assert on /state,
    // not on console output. See README.
    const std::string sentinel = std::format("__agentbridge_{}__", ++g_sentinelSeq);
    if (auto* log = RE::ConsoleLog::GetSingleton()) {
        log->Print("%s", sentinel.c_str());
    }

    script->SetCommand(a_command);
    script->CompileAndRun(a_target);
    result.ran = true;

    if (const char* msg = LastMessage()) {
        std::string after(msg, ::strnlen(msg, 0x400));
        // The engine appends a newline to what it stores; compare loosely.
        if (!after.empty() && after.find(sentinel) == std::string::npos) {
            result.output.push_back(std::move(after));
        }
    }

    // The Script form is ours, not the game's — it was never registered in a
    // form list, so nothing else will ever free it.
    delete script;

    return result;
}
