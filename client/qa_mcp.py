#!/usr/bin/env python3
"""qa_mcp — the QA loop as an MCP server, so Claude stops shelling out to curl.

Phase 2.2. Registered alongside houseCARL in ~/.claude.json.

Nine tools, chosen by how often they get called rather than by what exists:

  qa_status   is the game up, is the profile safe to edit
  qa_state    the /state snapshot — the thing assertions are written against
  qa_console  run a console command
  qa_actor    locate/move to/start dialogue with an actor by name or FormID
  qa_dialogue select or close structured dialogue
  qa_message_box select a guarded modal button
  qa_global   read a TESGlobal by EditorID
  qa_wait     wait until structured game-state conditions become true
  qa_run      execute a qa.json and return the report

Deliberately NOT exposed: install / uninstall / launch / kill. Those are one Bash
call each, they happen a handful of times per session, and a model that can end
the user's game session with a single tool call is worse ergonomics than one that
has to type the command. `qa_run` still does all of them — but from a qa.json the
user can read first.

Speaks MCP over stdio: one JSON-RPC object per line, stdout is protocol traffic
ONLY. Anything printed to stdout that isn't a response corrupts the stream, so
diagnostics go to stderr. stdlib only, matching the rest of client/.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge
import mo2ctl
import qa_runner

SERVER_NAME = "skyrim-qa"
SERVER_VERSION = "0.4.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

INCLUDE_VALUES = ["nearby_actors", "cell_actors", "loaded_actors", "inventory", "quests", "plugins"]

TOOLS = [
    {
        "name": "qa_status",
        "description": (
            "Whether Skyrim and MO2 are running, whether the in-game bridge answers, "
            "and whether MO2's profile is currently safe to edit. Cheap; call it before "
            "assuming the game is up."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mod": {"type": "string",
                        "description": "Optional MO2 mod name to also report on (installed / enabled / priority)."},
            },
        },
    },
    {
        "name": "qa_state",
        "description": (
            "Snapshot of live game state. `player` and `game` always come back; the rest "
            "are opt-in via `include` because a full inventory or quest sweep is far more "
            "work on the game thread than the player block.\n"
            "This is what QA assertions should be written against — never console output.\n"
            "Two gotchas: `equipped` covers hands only (armour appears as `worn: true` in "
            "`inventory`), and prefer `cell_form_id` over `cell` — EditorID strings come "
            "from whichever plugin wins the record and can be blank."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include": {"type": "array", "items": {"type": "string", "enum": INCLUDE_VALUES},
                            "description": "Optional blocks. `plugins` is the load order as the engine resolved it."},
                "radius": {"type": "number", "description": "nearby_actors search radius in game units (default 4096)."},
                "limit": {"type": "integer", "description": "Cap per collection (default 32). Ignored by `plugins`."},
            },
        },
    },
    {
        "name": "qa_console",
        "description": (
            "Run a Skyrim console command in the running game. Anything the console can do, "
            "including `load <save>` to load a baseline.\n"
            "The returned `output` is at most the console's LAST line and is not trustworthy: "
            "other plugins in this load order write to the console constantly, so "
            "`output_captured: true` does not mean the line came from your command. Treat it "
            "as a diagnostic and verify the effect with qa_state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "The command, e.g. 'player.additem f 100'."},
                "ref": {"type": "string",
                        "description": "Optional selected reference FormID (hex like '0x14' or decimal), for dotted commands."},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "qa_actor",
        "description": (
            "Operate on an actor by exact display name or runtime reference FormID without "
            "desktop input. `move_to` places the player beside and facing the actor and may "
            "cross cells; `activate` starts dialogue once both are in the current cell. "
            "Use scope=loaded for actors in Skyrim's active process lists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["move_to", "activate"]},
                "name": {"type": "string", "description": "Exact actor display name (case-insensitive)."},
                "form_id": {"oneOf": [{"type": "string"}, {"type": "integer"}],
                            "description": "Runtime reference FormID, e.g. '0x02001234'. Use instead of name."},
                "scope": {"type": "string", "enum": ["cell", "loaded"],
                          "description": "Name search scope; default cell. FormID + loaded can address a persistent reference."},
                "distance": {"type": "number", "description": "For move_to: 32-2048 game units; default 128."},
                "retry_for": {"type": "number", "description": "Retry while an actor/cell is still loading; default 0 seconds."},
                "retry_interval": {"type": "number", "description": "Seconds between retries; default 1."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "qa_dialogue",
        "description": (
            "Select a visible player dialogue option by displayed text, or close the "
            "current dialogue, without keyboard/mouse input. Read visible choices from "
            "qa_state().game.dialogue.options first. Exact matching is the default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["select", "close"]},
                "text": {"type": "string", "description": "Required for select."},
                "index": {"type": "integer", "minimum": 0,
                          "description": "Visible option index; use instead of text."},
                "info_form_id": {"oneOf": [{"type": "string"}, {"type": "integer"}],
                                 "description": "TopicInfo runtime FormID; use instead of text."},
                "contains": {"type": "boolean", "description": "Use a unique substring instead of exact text."},
                "retry_for": {"type": "number", "description": "Wait for the option/menu to appear; default 0 seconds."},
                "retry_interval": {"type": "number", "description": "Seconds between retries; default 1."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "qa_global",
        "description": "Read a live TESGlobal value by EditorID without parsing console output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "editor_id": {"type": "string"},
            },
            "required": ["editor_id"],
        },
    },
    {
        "name": "qa_message_box",
        "description": (
            "Select a visible Skyrim MessageBox button without keyboard/mouse input. "
            "Read game.message_box from qa_state first. Provide exactly one of button "
            "text or zero-based index; `message` is an optional exact guard that prevents "
            "a different modal from being selected while waiting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Exact visible button text."},
                "index": {"type": "integer", "minimum": 0,
                          "description": "Visible button index; use instead of text."},
                "message": {"type": "string",
                            "description": "Optional exact modal message guard."},
                "retry_for": {"type": "number",
                              "description": "Wait for the exact modal/button; default 0 seconds."},
                "retry_interval": {"type": "number",
                                   "description": "Seconds between retries; default 1."},
            },
        },
    },
    {
        "name": "qa_wait",
        "description": (
            "Poll structured qa_state until every JSON-path expectation passes. Use this "
            "after doors, loads, dialogue actions, or delayed NPC package changes instead "
            "of sleeping. Conditions use the qa.json operators: eq, ne, gt, gte, lt, lte, "
            "contains, not_contains, matches, exists, and count_eq/count_gte/count_lte."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "expect": {"type": "object",
                           "description": "Map of dotted JSON paths to one-operator condition objects."},
                "include": {"type": "array", "items": {"type": "string", "enum": INCLUDE_VALUES}},
                "radius": {"type": "number"},
                "limit": {"type": "integer"},
                "retry_for": {"type": "number", "description": "Total wait budget; default 20 seconds."},
                "retry_interval": {"type": "number", "description": "Poll interval; default 1 second."},
            },
            "required": ["expect"],
        },
    },
    {
        "name": "qa_run",
        "description": (
            "Execute a qa.json test file end to end — install, launch, assert, tear down — "
            "and return the per-step report. Schema: client/QA-SCHEMA.md.\n"
            "This WILL close a running game and MO2 if the file says to, and it can take "
            "minutes (a cold launch is ~20s). Use dry_run first to validate the file without "
            "touching anything.\n"
            "Status is `pass`, `fail`, or `needs_human` — the last means every assertion "
            "passed but a handoff_user step asked for someone to look at something. Relay "
            "those messages to the user; the runner never judges anything visual itself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "Path to a .qa.json file."},
                "dry_run": {"type": "boolean", "description": "Validate only; touch nothing."},
            },
            "required": ["spec"],
        },
    },
]


class ToolError(Exception):
    """Reported back as an isError tool result rather than a protocol error."""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def tool_qa_status(args: dict) -> dict:
    try:
        return mo2ctl.cmd_status(mo2ctl.load_env(), SimpleNamespace(mod=args.get("mod")))
    except mo2ctl.Fail as exc:
        raise ToolError(str(exc)) from exc


def tool_qa_state(args: dict) -> dict:
    include = args.get("include") or []
    unknown = [i for i in include if i not in INCLUDE_VALUES]
    if unknown:
        raise ToolError(f"unknown include value(s): {unknown}. Valid: {INCLUDE_VALUES}")
    result = bridge.state(include, radius=args.get("radius"), limit=args.get("limit"))
    if not result.get("ok"):
        raise ToolError(
            f"/state unavailable: {result.get('error')}. "
            f"During a load screen or right after launch the game thread doesn't drain and "
            f"this 503s — that is expected. Check qa_status for liveness and retry."
        )
    return result


def tool_qa_console(args: dict) -> dict:
    cmd = args.get("cmd")
    if not cmd:
        raise ToolError("`cmd` is required")
    result = bridge.console(cmd, args.get("ref"))
    if not result.get("ok"):
        raise ToolError(f"console call failed: {result.get('error')}")
    return result


def tool_qa_actor(args: dict) -> dict:
    action, name, form_id = args.get("action"), args.get("name"), args.get("form_id")
    if (name is None) == (form_id is None):
        raise ToolError("provide exactly one of `name` or `form_id`")
    scope = args.get("scope", "cell")
    if scope not in ("cell", "loaded"):
        raise ToolError("`scope` must be `cell` or `loaded`")
    if action == "move_to":
        probe = lambda: bridge.move_to_actor(
            name, form_id=form_id, scope=scope, distance=args.get("distance", 128.0))
    elif action == "activate":
        probe = lambda: bridge.activate_actor(name, form_id=form_id, scope=scope)
    else:
        raise ToolError("`action` must be `move_to` or `activate`")
    result = qa_runner.retry_for_ok(
        probe, args.get("retry_for", 0), args.get("retry_interval", 1.0))
    if not result.get("ok"):
        raise ToolError(f"actor {action} failed after {result['attempts']} attempt(s): "
                        f"{result.get('error')}")
    return result


def tool_qa_dialogue(args: dict) -> dict:
    action = args.get("action")
    if action == "select":
        selectors = [key for key in ("text", "index", "info_form_id")
                     if args.get(key) is not None]
        if len(selectors) != 1:
            raise ToolError("select needs exactly one of `text`, `index`, or `info_form_id`")
        result = qa_runner.retry_for_ok(
            lambda: bridge.select_dialogue(
                args.get("text"), contains=args.get("contains", False),
                index=args.get("index"), info_form_id=args.get("info_form_id")),
            args.get("retry_for", 0), args.get("retry_interval", 1.0))
    elif action == "close":
        result = bridge.close_dialogue()
    else:
        raise ToolError("`action` must be `select` or `close`")
    if not result.get("ok"):
        available = result.get("available") or []
        suffix = f"; available={available}" if available else ""
        raise ToolError(f"dialogue {action} failed: {result.get('error')}{suffix}")
    return result


def tool_qa_wait(args: dict) -> dict:
    expect = args.get("expect")
    if not isinstance(expect, dict) or not expect:
        raise ToolError("`expect` must be a non-empty object")
    include = args.get("include") or []
    unknown = [item for item in include if item not in INCLUDE_VALUES]
    if unknown:
        raise ToolError(f"unknown include value(s): {unknown}. Valid: {INCLUDE_VALUES}")

    budget = max(0, args.get("retry_for", 20))
    interval = args.get("retry_interval", 1.0)
    started = time.time()
    deadline = started + budget
    attempts = 0
    failures = []
    while True:
        attempts += 1
        snapshot = bridge.state(include, radius=args.get("radius"), limit=args.get("limit"))
        if snapshot.get("ok"):
            try:
                failures = [failure for failure in
                            (qa_runner.check(snapshot, path, condition)
                             for path, condition in expect.items()) if failure]
            except qa_runner.ConfigError as exc:
                raise ToolError(f"invalid expectation: {exc}") from exc
            if not failures:
                return {"ok": True, "attempts": attempts,
                        "elapsed_s": round(time.time() - started, 1), "state": snapshot}
        if time.time() >= deadline:
            detail = "; ".join(
                f"{failure['path']} {failure['op']} {failure['expected']!r} "
                f"(actual {failure['actual']!r})" for failure in failures)
            if not snapshot.get("ok"):
                detail = f"state unavailable: {snapshot.get('error')}"
            raise ToolError(f"state conditions did not pass after {attempts} attempt(s) "
                            f"over {budget}s: {detail}")
        time.sleep(interval)


def tool_qa_global(args: dict) -> dict:
    editor_id = args.get("editor_id")
    if not editor_id:
        raise ToolError("`editor_id` is required")
    result = bridge.global_value(editor_id)
    if not result.get("ok"):
        raise ToolError(f"global read failed: {result.get('error')}")
    return result


def tool_qa_message_box(args: dict) -> dict:
    selectors = [key for key in ("text", "index") if args.get(key) is not None]
    if len(selectors) != 1:
        raise ToolError("provide exactly one of `text` or `index`")
    if "text" in selectors and not args["text"]:
        raise ToolError("`text` must not be empty")
    if "index" in selectors and (isinstance(args["index"], bool) or
                                 not isinstance(args["index"], int) or args["index"] < 0):
        raise ToolError("`index` must be a non-negative integer")
    result = qa_runner.retry_for_ok(
        lambda: bridge.select_message_box(
            args.get("text"), index=args.get("index"), message=args.get("message")),
        args.get("retry_for", 0), args.get("retry_interval", 1.0))
    if not result.get("ok"):
        available = result.get("available") or []
        suffix = f"; available={available}" if available else ""
        raise ToolError(f"message box selection failed after {result['attempts']} attempt(s): "
                        f"{result.get('error')}{suffix}")
    return result


def tool_qa_run(args: dict) -> dict:
    spec_path = Path(args["spec"]).expanduser()
    if not spec_path.is_absolute():
        spec_path = (Path(__file__).resolve().parent / spec_path).resolve()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"cannot read {spec_path}: {exc}") from exc

    problems = qa_runner.validate(spec, spec_path.parent)
    if problems:
        raise ToolError("qa.json is not valid:\n  - " + "\n  - ".join(problems))
    if args.get("dry_run"):
        total = len(spec.get("steps", [])) + len(spec.get("teardown", []))
        return {"valid": True, "steps": total, "name": spec.get("name", spec_path.stem)}

    # interactive=False always: there is no terminal on the other end of a stdio
    # MCP pipe, and a runner that blocked on input() here would hang the server
    # with no way for the user to answer it.
    return qa_runner.Runner(spec, spec_path.parent, interactive=False).run()


HANDLERS = {
    "qa_status": tool_qa_status,
    "qa_state": tool_qa_state,
    "qa_console": tool_qa_console,
    "qa_actor": tool_qa_actor,
    "qa_dialogue": tool_qa_dialogue,
    "qa_message_box": tool_qa_message_box,
    "qa_global": tool_qa_global,
    "qa_wait": tool_qa_wait,
    "qa_run": tool_qa_run,
}


# ---------------------------------------------------------------------------
# JSON-RPC / MCP
# ---------------------------------------------------------------------------


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(text: str) -> None:
    print(f"[qa_mcp] {text}", file=sys.stderr, flush=True)


def handle_initialize(params: dict) -> dict:
    asked = params.get("protocolVersion")
    return {
        "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_tools_call(params: dict) -> dict:
    name = params.get("name")
    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name!r}"}], "isError": True}
    try:
        result = handler(params.get("arguments") or {})
        text = json.dumps(result, indent=1, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}]}
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception:
        log(traceback.format_exc())
        return {"content": [{"type": "text", "text": traceback.format_exc(limit=3)}], "isError": True}


def dispatch(method: str, params: dict):
    if method == "initialize":
        return handle_initialize(params)
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return handle_tools_call(params)
    if method == "ping":
        return {}
    raise KeyError(method)


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {exc}"}})
            continue

        method, msg_id = message.get("method"), message.get("id")
        # No id means a notification: acknowledge nothing, and in particular do
        # not reply to notifications/initialized — a response to a notification
        # is a protocol violation some clients disconnect over.
        if msg_id is None:
            continue

        try:
            send({"jsonrpc": "2.0", "id": msg_id, "result": dispatch(method, message.get("params") or {})})
        except KeyError:
            send({"jsonrpc": "2.0", "id": msg_id,
                  "error": {"code": -32601, "message": f"method not found: {method}"}})
        except Exception as exc:
            log(traceback.format_exc())
            send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}})
    return 0


if __name__ == "__main__":
    sys.exit(serve())
