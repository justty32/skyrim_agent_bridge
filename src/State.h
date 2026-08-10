#pragma once

#include <cstddef>

// Building the /state snapshot.
//
// Split from Routes.cpp because this is where the field set will keep growing,
// and because it is the only place in the plugin that reads broadly across the
// engine — worth keeping the transport and the route table free of it.
namespace State {
    // Player + game blocks are always included: they are small, always
    // available, and are what assertions are actually written against. The
    // expensive blocks are opt-in per request — a full inventory or the quest
    // sweep is orders of magnitude more work than the player block, and a QA
    // step that only checks "did the cell change" should not pay for it.
    struct Options {
        bool nearby = false;
        bool cellActors = false;
        bool inventory = false;
        bool quests = false;
        bool plugins = false;     // the load order as the engine actually resolved it —
                                  // this is what proves `mo2ctl install` took effect,
                                  // and it ignores `limit` because a truncated load
                                  // order would answer "is my plugin loaded" wrongly
        float radius = 4096.0f;   // ~2 Skyrim "units" per foot; this is a short walk
        std::size_t limit = 32;   // cap per collection, so one bad request can't
                                  // stall the game thread building a 900-entry array
    };

    // MUST be called on the game thread.
    nlohmann::json Snapshot(const Options& options);
}
