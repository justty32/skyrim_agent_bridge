#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace Screenshot {
struct Result {
    std::vector<std::uint8_t> bytes;
    std::string error;
    std::uint64_t requestId{0}, frameId{0};
    std::uint32_t width{0}, height{0};
};
bool Install();
void Stop();
Result Capture();
}
