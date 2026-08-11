#pragma once

#include <nlohmann/json.hpp>

// Structured access to Skyrim's modal MessageBoxMenu. Both functions must run
// on the game thread; they only inspect/invoke the active Scaleform menu and
// never synthesize desktop input.
namespace StructuredMessageBox {
    struct Selector {
        std::string text;
        std::optional<std::size_t> index;
        std::optional<std::string> expectedMessage;
    };

    nlohmann::json Snapshot();
    nlohmann::json Select(const Selector& a_selector);
}
