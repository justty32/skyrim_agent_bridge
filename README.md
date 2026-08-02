# agent-bridge

SKSE plugin that opens a localhost HTTP server **inside the running Skyrim process**, so a
Linux-side agent can read game state, drive the console, grab screenshots and hand the
keyboard back to a human — without touching the OS input/screen layer at all.

The eyes and hands go *into* the game process. See
[`workflows/plans/ai-ingame-qa-loop.md`](../../workflows/plans/ai-ingame-qa-loop.md)
(decision D1) for why: the host is Wayland with no screenshot tool installed, `xdotool` is
useless against non-XWayland windows, and the game lives behind Proton's pressure-vessel —
"screenshot the screen and fake keypresses" would be fragile and unreproducible.

## Why a sibling of `scene-capture-bridge` and not part of it

Decided 2026-08-02 (plan Phase 1.1). Both are SKSE C++23 DLLs on the same toolchain, and
`scene-capture-bridge` already has the cell-walking / JSON-export code this will eventually
want. But they have opposite lifecycles: `scene-capture-bridge` is an **authoring** tool a
human drives with hotkeys and an ImGui panel, shipped alongside content; `agent-bridge` is
**test harness** that must be installable and removable per QA run and must never end up in
a player-facing load order. Folding a listening socket into the authoring tool would mean
every content session also opens a port that can run console commands.

Code reuse, when it comes, goes the other way: lift the scene-walking routines into
`agent-bridge` as needed rather than merging the two plugins.

## Status

Phase 0.1 skeleton. Working today:

| Route | Runs on | Notes |
|---|---|---|
| `GET /ping` | socket thread | Liveness. Answers during load screens on purpose — lets the runner tell "process alive, game busy" from "process dead". |
| `GET /state` | game thread | `?include=nearby,inventory,quests,plugins&radius=&limit=`. Player + game blocks always; the rest opt-in. Two gotchas: `equipped` is **hands only** (armour shows as `worn: true` in `inventory`), and at the main menu this can 503 while the task queue isn't draining — that's expected, use `/ping` for liveness. |
| `POST /console` | game thread | `{"cmd": "...", "ref": "0x14"}`. `ref` is optional — it's the console's selected reference, for dotted commands. Output capture is one line and best-effort; see the pitfall below. |

Loading a save is just `{"cmd": "load <save filename without extension>"}` — verified working
from the main menu, so there's no separate autoload mechanism to build.

`include=plugins` returns the load order **as the engine resolved it**, which is the
thing to assert against after installing a mod — `plugins.txt` says what was asked for,
this says what happened. `index` is the byte a FormID actually carries (`0x00`–`0xFD`
for full plugins, `0xFE000`+ for light ones), so it doubles as the FormID prefix.

Not built yet: `POST /screenshot`, `POST /input` — both deferred, see plan decision D6.

The Linux side of all this lives in [`client/`](client/README.md): `mo2ctl.py` installs
and removes mods and starts the game with no MO2 GUI anywhere in the loop, `qa_runner.py`
executes a whole test from one [`qa.json`](client/QA-SCHEMA.md), and `qa_mcp.py` exposes
the frequently-called half of that to Claude as MCP tools.

## Design notes

**Port 5099, loopback only.** `INADDR_LOOPBACK`, never `INADDR_ANY` — this thing executes
console commands, so it must not be reachable from the network. The Linux client hardcodes
the same port; changing it is a two-sided edit.

**Two threads, one seam.** The accept loop runs on its own thread; nearly every `RE::` read
is only safe on the game's main thread. Routes that need game state hand a callable to
`GameThread::Run`, which marshals through SKSE's task interface and **times out** (3s
default) — during a load screen the task queue may not drain at all, and a blocked handler
would wedge the socket thread and make the bridge look dead. Timeout answers 503; the
runner retries.

**Hand-rolled HTTP, no cpp-httplib.** The surface is a handful of localhost JSON routes
called by one client. Every dependency added here has to survive the clang-cl + lld-link +
xwin cross-compile; ~200 lines of winsock is cheaper than that risk. One connection at a
time, `Connection: close`, 1 MiB request cap.

**No clean shutdown path.** SKSE has no unload message; the thread lives until the process
dies. `Http::Stop()` exists for completeness and is currently unused.

## Pitfall: do not hook `ConsoleLog::VPrint`

Tried on 2026-08-02. **It crashed the game on startup**, ~6.6s in, during Papyrus VM
init:

```
Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x000158B3D6AE
Access Violation: Tried to execute memory at 0x000158B3D6AE
[ 0][P] 0x000158B3D6AE
[ 1][S] 0x6FFFEA014404   AgentBridge.dll+0054404
[ 2][S] 0x6FFFE9819F94   ConsoleUtilSSE.dll+00B9F94
```

The detour itself installed fine and got called; it blew up **calling through to the
original**. `write_branch<5>` saves the 5 bytes it overwrites and jumps back to them, so
"jump to an unreadable address" means those saved bytes weren't the real prologue any more.

This load order already contains **`MoreInformativeConsole.dll`** and **`ConsoleUtilSSE.dll`**,
both of which sit on the console output path. Two plugins branch-patching the same five
bytes is enough: the second one overwrites the first's patch, and the first's saved
"original bytes" are now half of somebody else's `jmp`.

Generalise from this, don't just avoid this one function: **a five-byte prologue detour on a
popular engine function is not safe in a real 100-mod load order.** If output capture has to
get better than one line, the options in order of preference are (a) read more of
`ConsoleLog`'s own state, (b) go through a plugin that already owns the hook and exposes an
API, (c) hook a call site that no one else wants — never (d) race other plugins for the same
prologue.

What ships instead: `Console::Execute` prints a sentinel line, runs the command, then reads
`ConsoleLog::lastMessage` and returns it unless the sentinel is still sitting there. Plain
struct member access, nothing to collide with.

The sentinel is not decoration. The first attempt just snapshotted `lastMessage` before and
after and returned it if it changed — and the test run caught that lying: `load` and `coc`
print nothing, yet both came back with a line (`GetInFaction >> 0.00`, `IsShieldOut >> 0.00`)
that another mod had written in between. Something in this load order queries the console at
high frequency. Comparing against a line we wrote ourselves turns "nothing printed" back into
an empty result.

Two limits remain, both accepted:

- **One line only.** `sqs` and `help` come back as their last line.
- **The sentinel only holds for fast commands.** Measured on 0.3.0: `player.additem` and
  `player.setav` correctly return an empty `output`, but `load` and `coc` still leaked
  (`GetInFaction >> 0.00`, `GetNumericPackageData >> 360.00`). The longer a command's
  synchronous span, the more chance a foreign print lands inside it — and that span is a
  property of the command, not something this code can shrink.

So: **assert on `/state`, not on console output.** Treat the output field as a diagnostic,
never as the source of truth. `output_captured: true` does not mean the line came from your
command.

## Pitfall: `winsock2.h` goes *after* CommonLib, never before

The usual Windows advice is "include winsock2.h first, before anything drags in windows.h."
That is exactly backwards here, and it costs a build if you follow it. CommonLibSSE-NG ships
its own Win32 re-declarations (`REX::W32`), and `REX/W32/BASE.h` hard-errors on sight of a
real Windows header:

```
error: Windows API detected. Please move any Windows API includes after CommonLib, or remove them.
```

followed by a cascade — `inline constexpr auto MAX_PATH{260u}` can't parse once
`minwindef.h` has `#define MAX_PATH 260`. So `src/PCH.h` puts `RE/Skyrim.h` first and the
socket headers after. The reverse order is safe because macros only affect *later* parsing,
and `REX::W32`'s names are namespaced.

## Build

Linux host, cross-compiled to a Windows DLL — see plan decision D3: this is an internal
tool, not a player-facing product, so it ships straight from `clang-cl` without going
through Windows CI. Iteration speed wins.

Requires `xwin` splatted to `~/.xwin-cache` and `VCPKG_ROOT` set:

```bash
export VCPKG_ROOT="$HOME/vcpkg" && cmake --preset build-release-clang-cl-linux && cmake --build build/release-clang-cl-linux
```

Output: `build/release-clang-cl-linux/AgentBridge.dll`.

Optional auto-deploy: set `SKYRIM_MODS_FOLDER` (MO2 `mods/` dir) or `SKYRIM_FOLDER` before
configuring and the post-build step drops the DLL into `SKSE/Plugins/`.

## Verifying it works

With the game running:

```bash
curl -s 127.0.0.1:5099/ping && echo && curl -s 127.0.0.1:5099/state
```

The `127.0.0.1` reachability across the Proton boundary is not an assumption — it was
measured on 2026-08-02 with a standalone Win64 probe under both plain wine and Proton 9 +
pressure-vessel, and the listening socket was confirmed to belong to a `wineserver` inside
the container. Details in the plan, section "0.1a 實測結果".
