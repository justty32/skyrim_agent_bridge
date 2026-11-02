#pragma once

#include "GameActions.h"

// Internal selector/identity helpers shared by actions and diagnostics.
// Call only on the game thread; returned actor pointers do not cross tasks.
namespace ActorQueries {
    struct ActorLookup {
        RE::Actor* actor{ nullptr };
        std::size_t matches{ 0 };
    };

    std::string Fold(std::string_view a_text);
    nlohmann::json ActorRef(RE::Actor* a_actor);
    ActorLookup FindActor(const GameActions::ActorSelector& a_selector);
    nlohmann::json LookupError(const GameActions::ActorSelector& a_selector, const ActorLookup& a_lookup);
}
