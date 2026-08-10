#pragma once

#include <nlohmann/json.hpp>

// Semantic game actions used by the HTTP routes. Every function is called on
// Skyrim's game thread; none of them is safe from the socket thread directly.
namespace GameActions {
    struct ActorSelector {
        std::string name;
        RE::FormID formID{ 0 };
        bool loadedScope{ false };
    };

    struct DialogueSelector {
        std::string text;
        bool contains{ false };
        std::optional<std::size_t> index;
        RE::FormID infoFormID{ 0 };
    };

    nlohmann::json MoveToActor(const ActorSelector& a_selector, float a_distance);
    nlohmann::json ActivateActor(const ActorSelector& a_selector);
    nlohmann::json SelectDialogue(const DialogueSelector& a_selector);
    nlohmann::json CloseDialogue();
    nlohmann::json ReadGlobal(std::string_view a_editorID);
}
