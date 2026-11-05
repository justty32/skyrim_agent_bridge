#pragma once
#include <cstdint>
#include <cstring>
#include <optional>
#include <span>
namespace Screenshot::Hook {
using Render = std::int64_t (*)(std::int64_t);
inline std::int64_t Invoke(Render predecessor, std::int64_t argument, void (*capture)() noexcept) {
    const auto result = predecessor(argument);
    capture();
    return result;
}
inline std::optional<std::uintptr_t> CallTarget(std::uintptr_t address, std::span<const std::uint8_t> code) {
    if (code.size() < 5 || code[0] != 0xe8) return std::nullopt;
    std::int32_t displacement;
    std::memcpy(&displacement, code.data() + 1, sizeof(displacement));
    return address + 5 + displacement;
}
}
