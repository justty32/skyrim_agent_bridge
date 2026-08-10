#pragma once

#include <nlohmann/json.hpp>

// Semantic game actions used by the HTTP routes. Every function is called on
// Skyrim's game thread; none of them is safe from the socket thread directly.
namespace GameActions {
    nlohmann::json MoveToActor(std::string_view a_name, float a_distance);
    nlohmann::json ActivateActor(std::string_view a_name);
    nlohmann::json SelectDialogue(std::string_view a_text, bool a_contains);
    nlohmann::json CloseDialogue();
    nlohmann::json ReadGlobal(std::string_view a_editorID);
}
