"""Talking to the in-game HTTP bridge.

One module so the port and the route shapes are stated once. `mo2ctl`, the qa
runner and (later) the MCP server all come through here.

Every call returns a plain dict and never raises for a dead bridge — the caller
is usually deciding whether the game is up, and an exception is a clumsy way to
answer that. Transport failures come back as `{"ok": False, "error": ...}`.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://127.0.0.1:5099"

# The DLL binds INADDR_LOOPBACK deliberately — it runs console commands, so it must
# not be reachable off-box. Changing the port is a two-sided edit; see plugin.cpp.
DEFAULT_TIMEOUT = 15.0

_ACTOR_VALUE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ACTOR_VALUE_OUTPUT = re.compile(
    r"^GetActorValue:\s*(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*>>\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$")


def _request(method: str, path: str, *, body: dict | None = None, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The bridge answers 503 with a JSON body when the game thread didn't
        # drain in time (load screens, main-menu startup). That body is more
        # useful than the status code, so keep it.
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc)}


def ping(timeout: float = 1.0) -> dict:
    return _request("GET", "/ping", timeout=timeout)


def reachable(timeout: float = 1.0) -> bool:
    return bool(ping(timeout).get("ok"))


def state(include: list[str] | None = None, *, radius: float | None = None,
          limit: int | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    query: dict[str, str] = {}
    if include:
        query["include"] = ",".join(include)
    if radius is not None:
        query["radius"] = str(radius)
    if limit is not None:
        query["limit"] = str(limit)
    path = "/state" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    return _request("GET", path, timeout=timeout)


def console(cmd: str, ref: str | None = None, *, timeout: float = 30.0) -> dict:
    """Run a console command.

    The `output` field is best-effort and NOT trustworthy as an assertion target:
    the bridge can only read the console's last line, and other plugins in a real
    load order write to it constantly. `output_captured: true` does not mean the
    line came from your command. Assert on `state()`.
    """
    body: dict = {"cmd": cmd}
    if ref:
        body["ref"] = ref
    return _request("POST", "/console", body=body, timeout=timeout)


def _actor_body(name: str | None, form_id: str | int | None, scope: str) -> dict:
    body: dict = {"scope": scope}
    if name is not None:
        body["name"] = name
    if form_id is not None:
        body["form_id"] = form_id
    return body


def move_to_actor(name: str | None = None, *, form_id: str | int | None = None,
                  scope: str = "cell", distance: float = 128.0,
                  timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Move the player beside an actor selected by exact name or runtime FormID."""
    body = _actor_body(name, form_id, scope)
    body["distance"] = distance
    return _request("POST", "/actor/move-to",
                    body=body, timeout=timeout)


def activate_actor(name: str | None = None, *, form_id: str | int | None = None,
                   scope: str = "cell", timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Activate an actor selected by exact name or runtime FormID as the player."""
    return _request("POST", "/actor/activate",
                    body=_actor_body(name, form_id, scope), timeout=timeout)


def select_dialogue(text: str | None = None, *, contains: bool = False,
                    index: int | None = None, info_form_id: str | int | None = None,
                    timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Select a visible dialogue option by text, display index, or TopicInfo FormID."""
    body: dict = {"contains": contains}
    if text is not None:
        body["text"] = text
    if index is not None:
        body["index"] = index
    if info_form_id is not None:
        body["info_form_id"] = info_form_id
    return _request("POST", "/dialogue/select",
                    body=body, timeout=timeout)


def close_dialogue(*, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """End the current player dialogue without desktop input."""
    return _request("POST", "/dialogue/close", body={}, timeout=timeout)


def select_message_box(text: str | None = None, *, index: int | None = None,
                       message: str | None = None,
                       timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Select a visible modal button, optionally guarded by its exact message."""
    body: dict = {}
    if text is not None:
        body["text"] = text
    if index is not None:
        body["index"] = index
    if message is not None:
        body["message"] = message
    return _request("POST", "/messagebox/select", body=body, timeout=timeout)


def global_value(editor_id: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Read a TESGlobal by EditorID without relying on console output."""
    query = urllib.parse.urlencode({"editor_id": editor_id})
    return _request("GET", f"/global?{query}", timeout=timeout)


def actor_value(name: str, ref: str, *, consecutive: int = 3,
                max_attempts: int = 12, interval: float = 0.1,
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Read one actor value through narrowly validated console output.

    Console output is normally diagnostic-only because another plugin can race the
    bridge's last-line capture. This helper is the deliberately narrow exception: it
    accepts only the engine's exact ``GetActorValue: NAME >> NUMBER`` shape, requires
    the echoed actor-value name to match, and requires repeated identical values.
    Callers still choose the exact runtime reference (for example player ``0x14`` or
    Lydia ``0x000A2C94``).
    """
    if not _ACTOR_VALUE_NAME.fullmatch(name):
        return {"ok": False, "error": f"invalid actor value name: {name!r}"}
    if not isinstance(ref, str) or not ref.strip():
        return {"ok": False, "error": "ref must be a non-empty runtime FormID string"}
    if consecutive < 2:
        return {"ok": False, "error": "consecutive must be at least 2"}
    if max_attempts < consecutive:
        return {"ok": False, "error": "max_attempts must be >= consecutive"}
    if interval < 0:
        return {"ok": False, "error": "interval must be non-negative"}

    accepted: list[dict] = []
    rejected: list[dict] = []
    last_value: float | None = None
    streak = 0

    for attempt in range(1, max_attempts + 1):
        result = console(f"getav {name}", ref=ref, timeout=timeout)
        output = result.get("output")
        line = output[0].strip() if isinstance(output, list) and len(output) == 1 \
            and isinstance(output[0], str) else None
        match = _ACTOR_VALUE_OUTPUT.fullmatch(line or "")

        reason = None
        value = None
        if not result.get("ok"):
            reason = result.get("error", "console request failed")
        elif line is None:
            reason = "console output was not exactly one line"
        elif match is None:
            reason = "console line did not match GetActorValue format"
        elif match.group("name").casefold() != name.casefold():
            reason = "console echoed a different actor value"
        else:
            value = float(match.group("value"))
            if not math.isfinite(value):
                reason = "console returned a non-finite value"

        if reason is not None:
            rejected.append({"attempt": attempt, "reason": reason, "output": output})
            streak = 0
            last_value = None
        else:
            streak = streak + 1 if value == last_value else 1
            last_value = value
            accepted.append({"attempt": attempt, "value": value, "output": line})
            if streak >= consecutive:
                return {
                    "ok": True,
                    "actor_value": name,
                    "ref": ref,
                    "value": value,
                    "consecutive": streak,
                    "accepted": accepted,
                    "rejected": rejected,
                }

        if attempt < max_attempts and interval:
            time.sleep(interval)

    return {
        "ok": False,
        "error": f"no {consecutive} consecutive identical validated reads "
                 f"within {max_attempts} attempts",
        "actor_value": name,
        "ref": ref,
        "accepted": accepted,
        "rejected": rejected,
    }
