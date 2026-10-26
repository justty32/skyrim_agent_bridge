"""houseCARL stdio client 與 static-gates 命令。"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from pathlib import Path

from mo2ctl_crash import (  # noqa: F401
    crash_triage_from_capture,
)
from mo2ctl_env import (  # noqa: F401
    DEFAULT_HOUSECARL_SERVER, Env, Fail, HOUSECARL_MCP_PROTOCOL, HOUSECARL_REQUEST_TIMEOUT_ENV,
    STATIC_GATE_FORMAT,
)
from mo2ctl_files import (  # noqa: F401
    utc_stamp,
)
from mo2ctl_static_gates import (  # noqa: F401
    evaluate_static_gates,
)


# ---------------------------------------------------------------------------
# houseCARL static gates
# ---------------------------------------------------------------------------


class HousecarlClient:
    """Tiny stdio MCP client for the local houseCARL server.

    This keeps `mo2ctl static-gates` runnable as a normal CLI command instead of
    relying on the chat session's MCP transport. It speaks only the subset needed
    here: initialize and tools/call.
    """

    def __init__(self, env: Env, server: Path | None = None,
                 request_timeout: float | None = None):
        self.env = env
        self.server = (server or DEFAULT_HOUSECARL_SERVER).expanduser()
        timeout_value = (request_timeout if request_timeout is not None else
                         os.environ.get(HOUSECARL_REQUEST_TIMEOUT_ENV, "120"))
        try:
            self.request_timeout = float(timeout_value)
        except (TypeError, ValueError) as exc:
            raise Fail(f"{HOUSECARL_REQUEST_TIMEOUT_ENV} must be a number") from exc
        if self.request_timeout <= 0:
            raise Fail(f"{HOUSECARL_REQUEST_TIMEOUT_ENV} must be positive")
        self.proc: subprocess.Popen | None = None
        self._stdout_buffer = b""
        self.next_id = 1

    def __enter__(self):
        if not self.server.is_file():
            raise Fail(f"houseCARL server not found: {self.server} (use --housecarl-server)")
        child_env = os.environ.copy()
        child_env.setdefault("HOUSECARL_DATA_DIR", str(Path.home() / ".local/share/housecarl"))
        child_env["HouseCarl__DataDir"] = str(Path.home() / ".local/share/Steam/steamapps/common/Skyrim Special Edition/Data")
        child_env["HouseCarl__ModsDir"] = str(self.env.mods)
        child_env["HouseCarl__ProfileDir"] = str(self.env.profile_dir)
        self.proc = subprocess.Popen(
            [str(self.server)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env,
        )
        self.request("initialize", {
            "protocolVersion": HOUSECARL_MCP_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "mo2ctl", "version": "0"},
        })
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict) -> dict:
        msg_id = self.next_id
        self.next_id += 1
        deadline = time.monotonic() + self.request_timeout
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        assert self.proc is not None and self.proc.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            while True:
                newline = self._stdout_buffer.find(b"\n")
                if newline >= 0:
                    line = self._stdout_buffer[:newline]
                    self._stdout_buffer = self._stdout_buffer[newline + 1:]
                    if not line.strip():
                        continue
                    message = json.loads(line)
                    if message.get("id") != msg_id:
                        continue
                    if "error" in message:
                        raise Fail(f"houseCARL MCP error: {message['error']}")
                    return message.get("result") or {}
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise Fail(
                        f"houseCARL request timed out after {self.request_timeout:g}s: {method}"
                    )
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    if self._stdout_buffer:
                        self._stdout_buffer += b"\n"
                        continue
                    stderr = ""
                    if self.proc.stderr:
                        stderr = self.proc.stderr.read()
                    raise Fail(f"houseCARL server exited before replying: {stderr.strip()}")
                self._stdout_buffer += chunk

    def _send(self, message: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        parts = result.get("content") or []
        text = "\n".join(str(part.get("text", "")) for part in parts if part.get("type") == "text")
        return {
            "tool": name,
            "arguments": arguments,
            "is_error": bool(result.get("isError")),
            "text": text,
        }


def load_json_file(path: Path) -> dict:
    try:
        return json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Fail(f"cannot read JSON file {path}: {exc}") from exc


def write_json_file(path: Path, data: dict) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")


def static_tool_specs(args) -> list[tuple[str, dict]]:
    max_chars = args.max_chars
    plugins = getattr(args, "plugin", None)
    specs: list[tuple[str, dict]] = [
        ("housecarl_load_order_status", {"max_chars": max_chars}),
        ("housecarl_check_errors", {"plugins": plugins or None, "limit": args.limit, "max_chars": max_chars}),
        ("housecarl_skse_inventory", {"max_chars": max_chars}),
        ("housecarl_validate_scripts", {"plugins": plugins or None, "limit": args.limit, "max_chars": max_chars}),
    ]
    for formid in getattr(args, "dialogue_formid", None) or []:
        specs.append(("housecarl_validate_dialogue", {"formid": formid, "max_chars": max_chars}))
    assets = getattr(args, "asset", None)
    if assets:
        specs.append(("housecarl_asset_status", {"asset_paths": assets, "max_chars": max_chars}))
    for mesh in getattr(args, "mesh", None) or []:
        specs.append(("housecarl_nif_inspect", {"mesh_path": mesh, "sections": getattr(args, "nif_sections", ""), "max_chars": max_chars}))
    return specs


def capture_static_gates(env: Env, args) -> dict:
    results = []
    with HousecarlClient(env, Path(args.housecarl_server).expanduser() if args.housecarl_server else None) as hc:
        for tool, tool_args in static_tool_specs(args):
            results.append(hc.call_tool(tool, {k: v for k, v in tool_args.items() if v is not None}))
    return {
        "format": STATIC_GATE_FORMAT,
        "captured_at": utc_stamp(),
        "profile": env.profile,
        "plugins": args.plugin or [],
        "mod": args.mod,
        "tools": results,
        "crash_logs": crash_triage_from_capture(results, args),
    }


def cmd_static_gates(env: Env, args) -> dict:
    baseline = load_json_file(Path(args.baseline)) if args.baseline else None
    capture = capture_static_gates(env, args)
    evaluation = evaluate_static_gates(capture, baseline)
    report = {
        "format": STATIC_GATE_FORMAT,
        "generated_at": utc_stamp(),
        "profile": env.profile,
        "baseline": str(Path(args.baseline).expanduser()) if args.baseline else None,
        "capture": capture,
        "evaluation": evaluation,
    }
    if args.write_baseline:
        write_json_file(Path(args.write_baseline), capture)
        report["baseline_written"] = str(Path(args.write_baseline).expanduser())
    if args.report:
        write_json_file(Path(args.report), report)
        report["report_written"] = str(Path(args.report).expanduser())
    return {
        "status": evaluation["status"],
        "profile": env.profile,
        "gates": evaluation["gates"],
        "crash_logs": capture.get("crash_logs"),
        "baseline_written": report.get("baseline_written"),
        "report_written": report.get("report_written"),
    }
