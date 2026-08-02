#include "log.h"

#include "HttpServer.h"
#include "Routes.h"

namespace {
    // Fixed for now. If it ever needs to move, it moves to an ini — but the
    // Linux-side client hardcodes it too, so changing it is a two-sided edit.
    constexpr std::uint16_t kPort = 5099;
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
