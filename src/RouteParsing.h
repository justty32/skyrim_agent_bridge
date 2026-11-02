#pragma once

#include <charconv>
#include <optional>
#include <string_view>

// Internal shared parser: hexadecimal, whole input, no whitespace/truncation.
namespace RouteParsing {
    // "0x14" / "14" / "0X14" -> 0x14. The whole string must be valid.
    inline std::optional<RE::FormID> ParseFormID(std::string_view s)
    {
        if (s.starts_with("0x") || s.starts_with("0X")) {
            s.remove_prefix(2);
        }
        if (s.empty()) return std::nullopt;

        RE::FormID value = 0;
        const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), value, 16);
        if (ec != std::errc{} || ptr != s.data() + s.size()) return std::nullopt;
        return value;
    }

}
