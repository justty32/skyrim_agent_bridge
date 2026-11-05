#pragma once
#include "InputCore.h"
namespace Input {
bool Install();
bool Start();
void Stop();
Core::Result Tap(std::uint32_t scanCode);
}
