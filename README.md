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

Version 0.6.0. The current-cell, loaded-actor, cross-cell, retry, and structured dialogue
paths are runtime-verified.

| Route | Runs on | Notes |
|---|---|---|
| `GET /ping` | socket thread | Liveness. Answers during load screens on purpose — lets the runner tell "process alive, game busy" from "process dead". |
| `GET /state` | game thread | `?include=nearby_actors,cell_actors,loaded_actors,inventory,quests,plugins&radius=&limit=`. `loaded_actors` walks all four engine process lists, deduplicates them, and exposes cell/FormID/3D-loaded state. Player + game (including open dialogue and its options) always; the rest opt-in. Two gotchas: `equipped` is **hands only** (armour shows as `worn: true` in `inventory`), and at the main menu this can 503 while the task queue isn't draining — that's expected, use `/ping` for liveness. |
| `GET /global` | game thread | `?editor_id=...`; live TESGlobal value without parsing noisy console output. |
| `POST /console` | game thread | `{"cmd": "...", "ref": "0x14"}`. `ref` is optional — it's the console's selected reference, for dotted commands. Output capture is one line and best-effort; see the pitfall below. |
| `POST /actor/move-to` | game thread | `{"name":"Falas Indaryn","scope":"loaded","distance":128}` or `{"form_id":"0x02001234"}`. Exact name or stable runtime reference ID; `scope=loaded` searches Skyrim's actor process lists and movement can cross cells. |
| `POST /actor/activate` | game thread | Same actor selector. Starts normal player dialogue once the actor is loaded in the player's current cell. |
| `POST /dialogue/select` | game thread | Select one visible option by exactly one of `text`, zero-based `index`, or runtime `info_form_id`, through the Dialogue Menu's structured callback. |
| `POST /dialogue/close` | game thread | Ends the active player dialogue. |

Loading a save is just `{"cmd": "load <save filename without extension>"}` — verified working
from the main menu, so there's no separate autoload mechanism to build.

`include=plugins` returns the load order **as the engine resolved it**, which is the
thing to assert against after installing a mod — `plugins.txt` says what was asked for,
this says what happened. `index` is the byte a FormID actually carries (`0x00`–`0xFD`
for full plugins, `0xFE000`+ for light ones), so it doubles as the FormID prefix.

Not built yet: `POST /screenshot`, `POST /input` — both deferred, see plan decision D6.

The semantic actor/dialogue path was verified end to end on 2026-08-10: enumerate the
current cell, find and move beside Falas, start dialogue, read the displayed options,
select parley by exact text, and observe its TopicInfo script change a TESGlobal from
0 to 5. No screen capture, OCR, keyboard, or mouse event participates in that chain.

0.6.0 extends that path without changing the verified defaults: `scope=cell` and exact
name still behave as before. FormID selectors remove same-name ambiguity; `scope=loaded`
can find actors in the four `ProcessLists` buckets; a different-cell `move_to` first uses
Skyrim's native reference-to-reference move, then applies the requested standing offset.
The returned actor object states `cell_form_id`, `worldspace_form_id`, `loaded_3d`, and
`disabled`, so callers can distinguish "known reference" from "ready to talk". Live QA
must still establish which persistent unloaded references Skyrim can resolve in a given
load order; the API reports a clean not-found instead of pretending every NPC exists.
For `loaded_actors`, `distance` is geometrically meaningful only when `same_cell` is true;
different interiors do not share a useful coordinate space.

### 0.6.0 live acceptance (2026-08-10)

- Existing livingNpcs generic anchor/parley regression remained **31/31 PASS**.
- `loaded_actors` returned 1,024 process-list actors at the requested limit with no
  duplicate runtime FormIDs.
- Falas was moved to and activated by runtime reference FormID; the parley TopicInfo was
  selected once by display index and once by `info_form_id`. Both executions changed the
  favor TESGlobal from 0 to 5, proving the TIF ran.
- From the Bannered Mare, moving to unloaded persistent reference Lucan Valerius crossed
  into Riverwood Trader; `loaded_3d` changed from false to true, state converged in 0.3s,
  and dialogue opened by FormID.
- `scope=loaded` name lookup followed Falas from Riverwood to his live exterior package
  location (`WhiterunWatchtowerExterior02`) rather than assuming his configured anchor.
- A deliberately delayed Riverwood transition made a current-cell Bjorn move fail four
  times and succeed on attempt five; MCP `qa_wait` then confirmed cell, actor, and dialogue
  conditions without fixed sleeps.

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
