#pragma once

#include <nlohmann/json.hpp>

namespace StateActors {
    nlohmann::json Nearby(RE::PlayerCharacter* a_player, float a_radius, std::size_t a_limit);
    nlohmann::json CurrentCell(RE::PlayerCharacter* a_player, std::size_t a_limit);
}
