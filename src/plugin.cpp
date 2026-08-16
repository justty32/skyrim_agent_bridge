#include "log.h"

#include "HttpServer.h"
#include "Routes.h"
#include "State.h"

namespace {
    // Fixed for now. If it ever needs to move, it moves to an ini — but the
    // Linux-side client hardcodes it too, so changing it is a two-sided edit.
    constexpr std::uint16_t kPort = 5099;

    // SKSE's save hook dispatches `(void*)result` with dataLen 1. `data` is the
    // scalar 0/1 value itself, not an address that may be dereferenced.
    constexpr bool IsSuccessfulPostLoadGamePayload(
        std::uintptr_t a_data, std::uint32_t a_dataLen) noexcept
    {
        return a_dataLen == sizeof(bool) && a_data == 1;
    }

    static_assert(IsSuccessfulPostLoadGamePayload(1, sizeof(bool)));
    static_assert(!IsSuccessfulPostLoadGamePayload(0, sizeof(bool)));
    static_assert(!IsSuccessfulPostLoadGamePayload(2, sizeof(bool)));
    static_assert(!IsSuccessfulPostLoadGamePayload(1, 0));
}

void MessageHandler(SKSE::MessagingInterface::Message* a_msg)
{
    switch (a_msg->type) {
    case SKSE::MessagingInterface::kDataLoaded:
        // Started at kDataLoaded, not at plugin load: /state needs the game's
        // singletons to exist, and a bridge that answers before they do would
        // just hand the runner garbage.
        Routes::Register();
        if (!Http::Start(kPort)) {
            SKSE::log::error("AgentBridge: server did not start — QA loop is blind this session");
        }
        break;
    case SKSE::MessagingInterface::kPostLoadGame:
        // Advance only for SKSE's exact successful scalar payload so an
        // already-matching fingerprint cannot turn a failed load into a false
        // pass. Never dereference `data`: success is encoded as address value 1.
        if (IsSuccessfulPostLoadGamePayload(
                reinterpret_cast<std::uintptr_t>(a_msg->data), a_msg->dataLen)) {
            SKSE::log::info("AgentBridge: save load epoch {}", State::NotifySaveLoaded());
        } else {
            SKSE::log::warn(
                "AgentBridge: ignored unsuccessful or malformed kPostLoadGame payload (bytes={})",
                a_msg->dataLen);
        }
        break;
    default:
        break;
    }
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse);
    SetupLog();
    SKSE::log::info("AgentBridge loaded");

    auto* messaging = SKSE::GetMessagingInterface();
    if (!messaging->RegisterListener("SKSE", MessageHandler)) {
        SKSE::log::error("AgentBridge: failed to register SKSE message listener");
        return false;
    }
    return true;
}
