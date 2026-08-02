"""Talking to the in-game HTTP bridge.

One module so the port and the route shapes are stated once. `mo2ctl`, the qa
runner and (later) the MCP server all come through here.

Every call returns a plain dict and never raises for a dead bridge — the caller
is usually deciding whether the game is up, and an exception is a clumsy way to
answer that. Transport failures come back as `{"ok": False, "error": ...}`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://127.0.0.1:5099"

# The DLL binds INADDR_LOOPBACK deliberately — it runs console commands, so it must
# not be reachable off-box. Changing the port is a two-sided edit; see plugin.cpp.
DEFAULT_TIMEOUT = 15.0


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
