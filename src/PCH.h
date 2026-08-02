#pragma once

#include "RE/Skyrim.h"
#include "SKSE/SKSE.h"

// winsock2 MUST come after CommonLib, not before. CommonLibSSE-NG ships its own
// Win32 re-declarations (REX::W32) and BASE.h hard-errors with "Windows API
// detected" if a real Windows header got there first — its `inline constexpr
// auto MAX_PATH` etc. can't survive the macros from minwindef.h. The other way
// round is fine: by the time windows.h's macros exist, CommonLib is fully
// parsed, and REX::W32's names are namespaced so nothing collides.
//
// winsock2.h (not winsock.h) and before any windows.h, or we get the ancient
// 1.1 winsock declarations. WIN32_LEAN_AND_MEAN comes from the preset.
#include <winsock2.h>
#include <ws2tcpip.h>

#include <nlohmann/json.hpp>

using namespace std::literals;
