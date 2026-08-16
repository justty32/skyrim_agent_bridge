#!/usr/bin/env python3
"""mo2ctl — drive Mod Organizer 2 from the Linux side without opening its GUI.

The Linux half of the AI QA loop (plan: workflows/plans/ai-ingame-qa-loop.md, Phase 2.1).
Installs a mod folder into MO2, flips it on and off in the profile, starts SKSE through
MO2, kills the game, and reports whether any of that is currently safe to do.

stdlib only, on purpose: this runs before anything is built and has to keep working when
the rest of the toolchain is mid-rebuild.

  mo2ctl status [--json]
  mo2ctl inspect <archive-or-dir> [--write-choices PATH]
  mo2ctl install <archive-or-dir-or-esp> [--name NAME] [--priority bottom] [--fomod-choices PATH] [--no-enable] [--force]
  mo2ctl uninstall <name> [--keep-files]
  mo2ctl profile-status
  mo2ctl profile-semantics [--ref HEAD]
  mo2ctl profile-absorb-churn
  mo2ctl static-gates [--plugin PLUGIN] [--baseline before.json] [--report report.json]
  mo2ctl select-profile <profile>
  mo2ctl try-begin <name>
  mo2ctl try-fail [--uninstall MOD]
  mo2ctl try-pass [-m MESSAGE]
  mo2ctl enable <name>
  mo2ctl disable <name>
  mo2ctl launch [--wait SECONDS] [--no-wait] [--background-active]
  mo2ctl kill [--mo2]

Everything that mutates MO2 state refuses to run while MO2 or the game is up; see
`profile_lock_reason` for why that is not merely cautious.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

import bridge

DEFAULT_MO2_ROOT = Path.home() / "games/mod-organizer-2-skyrimspecialedition/modorganizer2"
DEFAULT_PROFILE = "Default"
STEAM_APPID = "489830"

PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
BSA_SUFFIXES = (".bsa", ".ba2")
FOMOD_CHOICES_FORMAT = "mo2ctl-fomod-choices-v1"
PROFILE_MANIFEST_FORMAT = "mo2ctl-profile-manifest-v1"
STATIC_GATE_FORMAT = "mo2ctl-static-gates-v1"
PROFILE_MAIN_BRANCH = "main"
DEFAULT_HOUSECARL_SERVER = Path.home() / "tools/housecarl/server/housecarl-mcp"
HOUSECARL_MCP_PROTOCOL = "2025-06-18"
ENGINE_LOADORDER_CHURN = {
    "ccbgssse068-bloodfall.esl",
    "ccbgssse069-contest.esl",
    "ccvsvsse004-beafarmer.esl",
}
BACKGROUND_ACTIVE_BACKUP = "background-active-skyrim.ini"

# Directory names that make a folder recognisable as a Skyrim `Data`-relative mod root.
DATA_DIR_NAMES = {
    "skse", "meshes", "textures", "scripts", "sound", "interface", "seq",
    "music", "video", "grass", "lodsettings", "shadersfx", "strings", "dyndolod",
    "netscriptframework", "source", "calientetools", "tools", "docs",
}


class Fail(Exception):
    """A problem worth reporting to the caller, not a traceback."""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


@dataclass
class Env:
    root: Path
    profile: str

    @property
    def mods(self) -> Path:
        return self.root / "mods"

    @property
    def profile_dir(self) -> Path:
        return self.root / "profiles" / self.profile

    @property
    def profiles_repo(self) -> Path:
        return self.root / "profiles"

    @property
    def manifest(self) -> Path:
        return self.profiles_repo / "manifest.json"

    @property
    def modlist(self) -> Path:
        return self.profile_dir / "modlist.txt"

    @property
    def plugins(self) -> Path:
        return self.profile_dir / "plugins.txt"

    @property
    def loadorder(self) -> Path:
        return self.profile_dir / "loadorder.txt"

    @property
    def archives(self) -> Path:
        return self.profile_dir / "archives.txt"

    @property
    def mo2_exe(self) -> Path:
        return self.root / "ModOrganizer.exe"


def load_env() -> Env:
    root = Path(os.environ.get("MO2_ROOT", DEFAULT_MO2_ROOT)).expanduser()
    if not root.is_dir():
        raise Fail(f"MO2 root not found: {root} (set MO2_ROOT)")

    profile = os.environ.get("MO2_PROFILE") or read_selected_profile(root) or DEFAULT_PROFILE
    env = Env(root=root, profile=profile)
    if not env.profile_dir.is_dir():
        raise Fail(f"profile not found: {env.profile_dir} (set MO2_PROFILE)")
    return env


def read_selected_profile(root: Path) -> str | None:
    """Pull `selected_profile` out of ModOrganizer.ini.

    MO2 stores it Qt-style as `selected_profile=@ByteArray(Default)`.
    """
    ini = root / "ModOrganizer.ini"
    if not ini.is_file():
        return None
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "selected_profile":
            continue
        value = value.strip()
        if value.startswith("@ByteArray(") and value.endswith(")"):
            value = value[len("@ByteArray("):-1]
        return value or None
    return None


# ---------------------------------------------------------------------------
# Profile files
#
# The three profile files do NOT agree on line endings — modlist.txt and
# loadorder.txt are CRLF, plugins.txt is LF. Writing the wrong one back is the
# kind of change that looks fine in a diff and then silently doesn't match when
# something greps for `^+Name$`. So every read carries its own ending along.
# ---------------------------------------------------------------------------


@dataclass
class TextFile:
    path: Path
    lines: list[str]
    eol: str
    trailing_eol: bool


def read_file(path: Path) -> TextFile:
    if not path.is_file():
        raise Fail(f"missing profile file: {path}")
    text = path.read_bytes().decode("utf-8", errors="replace")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines.pop()
    return TextFile(path=path, lines=lines, eol=eol, trailing_eol=trailing)


def write_file(tf: TextFile, *, backup: bool = True) -> Path | None:
    made = backup_file(tf.path) if backup else None
    text = tf.eol.join(tf.lines) + (tf.eol if tf.trailing_eol else "")
    tf.path.write_bytes(text.encode("utf-8"))
    return made


def atomic_write(path: Path, data: bytes) -> None:
    """Replace one file atomically, keeping temporary bytes on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def background_active_backup(env: Env) -> Path:
    return env.profile_dir / ".mo2ctl-backups" / BACKGROUND_ACTIVE_BACKUP


def restore_background_active(env: Env) -> dict:
    """Restore the exact pre-launch Skyrim.ini bytes after an unattended run."""
    backup = background_active_backup(env)
    if not backup.is_file():
        return {"restored": False}
    ini = env.profile_dir / "skyrim.ini"
    atomic_write(ini, backup.read_bytes())
    backup.unlink()
    return {"restored": True, "path": str(ini)}


def enable_background_active(env: Env) -> dict:
    """Temporarily keep Skyrim's game thread alive while its window is unfocused."""
    restore_background_active(env)  # recover an interrupted previous run first
    ini = env.profile_dir / "skyrim.ini"
    if not ini.is_file():
        raise Fail(f"missing profile file: {ini}")
    original = ini.read_bytes()
    backup = background_active_backup(env)
    atomic_write(backup, original)

    tf = read_file(ini)
    general = next((i for i, line in enumerate(tf.lines)
                    if line.strip().casefold() == "[general]"), None)
    if general is None:
        tf.lines[0:0] = ["[General]", "bAlwaysActive=1"]
    else:
        end = next((i for i in range(general + 1, len(tf.lines))
                    if tf.lines[i].strip().startswith("[")), len(tf.lines))
        setting = next((i for i in range(general + 1, end)
                        if tf.lines[i].partition("=")[0].strip().casefold()
                        == "balwaysactive"), None)
        if setting is None:
            tf.lines.insert(general + 1, "bAlwaysActive=1")
        else:
            tf.lines[setting] = "bAlwaysActive=1"
    text = tf.eol.join(tf.lines) + (tf.eol if tf.trailing_eol else "")
    try:
        atomic_write(ini, text.encode("utf-8"))
    except Exception:
        restore_background_active(env)
        raise
    return {"enabled": True, "path": str(ini), "backup": str(backup)}


def utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(env: Env) -> dict:
    if not env.manifest.is_file():
        return {
            "format": PROFILE_MANIFEST_FORMAT,
            "mods": {},
        }
    try:
        data = json.loads(env.manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Fail(f"cannot parse manifest: {env.manifest}: {exc}") from exc
    if data.get("format") != PROFILE_MANIFEST_FORMAT:
        raise Fail(f"unsupported manifest format in {env.manifest}")
    if not isinstance(data.get("mods"), dict):
        raise Fail(f"manifest mods must be an object: {env.manifest}")
    return data


def write_manifest(env: Env, data: dict) -> None:
    data["format"] = PROFILE_MANIFEST_FORMAT
    data["updated_at"] = utc_stamp()
    env.manifest.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                            encoding="utf-8")


def manifest_from_git(env: Env, ref: str) -> dict:
    proc = git_profiles(env, ["show", f"{ref}:manifest.json"], check=False)
    if proc.returncode != 0:
        return {"format": PROFILE_MANIFEST_FORMAT, "mods": {}}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Fail(f"cannot parse manifest from {ref}: {exc}") from exc
    if data.get("format") != PROFILE_MANIFEST_FORMAT:
        return {"format": PROFILE_MANIFEST_FORMAT, "mods": {}}
    if not isinstance(data.get("mods"), dict):
        return {"format": PROFILE_MANIFEST_FORMAT, "mods": {}}
    return data


def update_manifest_for_install(env: Env, result: dict, resolved: "ResolvedSource",
                                args) -> dict:
    manifest = read_manifest(env)
    source = Path(args.source).expanduser()
    digest = sha256_file(source)
    entry = {
        "name": result["installed"],
        "profile": env.profile,
        "enabled": result["enabled"],
        "priority": result["priority"],
        "version": resolved.version or args.version,
        "source_path": str(source),
        "source_url": getattr(args, "source_url", None) or None,
        "source_kind": "archive" if source.suffix.lower() in ARCHIVE_SUFFIXES else
                       "file" if source.is_file() else "directory",
        "sha256": digest,
        "archive_library": archive_library_status(digest),
        "plugins": result["plugins_found"],
        "archives": result["archives_found"],
        "fomod_choices": resolved.fomod_choices,
        "comment": args.comment,
        "installed_at": utc_stamp(),
    }
    manifest["mods"][result["installed"]] = entry
    write_manifest(env, manifest)
    return entry


def remove_manifest_entry(env: Env, name: str) -> bool:
    manifest = read_manifest(env)
    if name not in manifest["mods"]:
        return False
    del manifest["mods"][name]
    write_manifest(env, manifest)
    return True


def git_profiles(env: Env, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    if not (env.profiles_repo / ".git").is_dir():
        raise Fail(f"profile repo is not initialized: {env.profiles_repo}")
    proc = subprocess.run(
        ["git", *argv],
        cwd=env.profiles_repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(argv)} failed"
        raise Fail(msg)
    return proc


def git_branch(env: Env) -> str:
    return git_profiles(env, ["branch", "--show-current"]).stdout.strip()


def git_head(env: Env) -> str:
    return git_profiles(env, ["rev-parse", "--short", "HEAD"]).stdout.strip()


def git_porcelain(env: Env) -> list[str]:
    out = git_profiles(env, ["status", "--porcelain"]).stdout
    return [ln for ln in out.splitlines() if ln]


def dirty_paths(lines: list[str]) -> list[str]:
    paths = []
    for line in lines:
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            paths.append(path)
    return paths


def engine_churn_paths(env: Env) -> set[str]:
    return {
        f"{env.profile}/loadorder.txt",
        f"{env.profile}/plugins.txt",
    }


def absorb_engine_churn(env: Env, message: str | None = None, *, force: bool = False) -> dict:
    dirty = git_porcelain(env)
    if not dirty:
        return {"absorbed": False, "reason": "profile repo already clean"}

    paths = dirty_paths(dirty)
    unexpected = [p for p in paths if p not in engine_churn_paths(env)]
    before = profile_semantics(env, "HEAD")
    after = profile_semantics(env, None)
    diff = semantic_diff(before, after)
    if (unexpected or diff) and not force:
        raise Fail(
            "profile repo has non-churn changes; review before opening a try branch: "
            + json.dumps({"paths": unexpected, "semantic_diff": diff}, ensure_ascii=False)
        )

    git_profiles(env, ["add", *paths])
    git_profiles(env, ["commit", "-m", message or "Absorb MO2 engine churn"])
    return {
        "absorbed": True,
        "commit": git_head(env),
        "paths": paths,
        "semantic_diff": diff,
        "forced": force,
    }


def require_clean_profile_repo(env: Env) -> None:
    dirty = git_porcelain(env)
    if dirty:
        raise Fail("profile repo is dirty; run profile-absorb-churn if this is MO2 engine churn, or review it before starting a new branch")


def slug_branch(text: str) -> str:
    keep = []
    last_dash = False
    for ch in text.strip().lower():
        if ch.isalnum():
            keep.append(ch)
            last_dash = False
        elif not last_dash:
            keep.append("-")
            last_dash = True
    slug = "".join(keep).strip("-")
    return slug or "mod"


def text_file_from_string(path: Path, text: str) -> TextFile:
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines.pop()
    return TextFile(path=path, lines=lines, eol=eol, trailing_eol=trailing)


def profile_text_at(env: Env, ref: str | None, rel: str) -> str:
    path = env.profiles_repo / rel
    if ref is None:
        if not path.is_file():
            return ""
        return path.read_bytes().decode("utf-8", errors="replace")
    proc = git_profiles(env, ["show", f"{ref}:{rel}"], check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def profile_semantics(env: Env, ref: str | None = None) -> dict:
    profile_rel = f"{env.profile}/"
    modlist = text_file_from_string(env.modlist, profile_text_at(env, ref, profile_rel + "modlist.txt"))
    plugins = text_file_from_string(env.plugins, profile_text_at(env, ref, profile_rel + "plugins.txt"))
    loadorder = text_file_from_string(env.loadorder, profile_text_at(env, ref, profile_rel + "loadorder.txt"))
    archives = text_file_from_string(env.archives, profile_text_at(env, ref, profile_rel + "archives.txt"))

    mods = modlist_entries(modlist)
    enabled_mods = [name for name, enabled in mods if enabled]
    active_plugins = [
        ln.lstrip("*").strip()
        for ln in plugins.lines
        if ln.startswith("*") and ln.lstrip("*").strip()
    ]
    plugin_order = [
        ln.strip()
        for ln in loadorder.lines
        if ln.strip()
        and not ln.startswith("#")
        and ln.strip().lower() not in ENGINE_LOADORDER_CHURN
    ]
    archive_entries = [
        ln.strip().lstrip("*")
        for ln in archives.lines
        if ln.strip() and not ln.startswith("#")
    ]
    return {
        "enabled_mods": sorted(enabled_mods, key=str.lower),
        "mod_order": [name for name, _ in mods],
        "active_plugins": sorted(active_plugins, key=str.lower),
        "plugin_order": plugin_order,
        "archives": sorted(archive_entries, key=str.lower),
    }


def semantic_diff(before: dict, after: dict) -> dict:
    diff = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            diff[key] = {"before": before.get(key), "after": after.get(key)}
    return diff


def archive_library_status(sha256: str | None) -> str:
    if not sha256:
        return "not_applicable"
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256.lower()):
        return "unchecked"
    exe = shutil.which("mongosh") or shutil.which("mongo")
    if not exe:
        return "unchecked"
    js = f'const n=db.getSiblingDB("skyrim").archives.countDocuments({{_id:"{sha256.lower()}"}}); print(n);'
    try:
        proc = subprocess.run(
            [exe, "mongodb://127.0.0.1:27018/skyrim", "--quiet", "--eval", js],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=0.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unchecked"
    if proc.returncode != 0:
        return "unchecked"
    last = proc.stdout.strip().splitlines()[-1:] or [""]
    return "present" if last[0].strip() not in {"", "0"} else "missing"


# ---------------------------------------------------------------------------
# houseCARL static gates
# ---------------------------------------------------------------------------


class HousecarlClient:
    """Tiny stdio MCP client for the local houseCARL server.

    This keeps `mo2ctl static-gates` runnable as a normal CLI command instead of
    relying on the chat session's MCP transport. It speaks only the subset needed
    here: initialize and tools/call.
    """

    def __init__(self, env: Env, server: Path | None = None):
        self.env = env
        self.server = (server or DEFAULT_HOUSECARL_SERVER).expanduser()
        self.proc: subprocess.Popen | None = None
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
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = ""
                if self.proc.stderr:
                    stderr = self.proc.stderr.read()
                raise Fail(f"houseCARL server exited before replying: {stderr.strip()}")
            message = json.loads(line)
            if message.get("id") != msg_id:
                continue
            if "error" in message:
                raise Fail(f"houseCARL MCP error: {message['error']}")
            return message.get("result") or {}

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


def tool_key(result: dict) -> str:
    args = result.get("arguments") or {}
    suffix = ""
    if "formid" in args:
        suffix = f":{args['formid']}"
    if "mesh_path" in args:
        suffix = f":{args['mesh_path']}"
    if "asset_paths" in args:
        suffix = ":" + ",".join(args["asset_paths"])
    return f"{result.get('tool')}{suffix}"


def normalize_load_order_warnings(text: str) -> list[str]:
    warnings = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean.startswith("- "):
            continue
        lowered = clean.lower()
        if any(cc in lowered for cc in ENGINE_LOADORDER_CHURN):
            continue
        if "warning" in lowered or "load order lists" in lowered or "missing" in lowered:
            warnings.append(clean)
    return warnings


def skse_diagnostics(text: str) -> tuple[list[str], list[str]]:
    diagnostics = []
    incomplete = []
    section = None
    expected = None
    seen = 0

    def finish_section() -> None:
        nonlocal section, expected, seen
        if section is not None and expected is not None and seen != expected:
            incomplete.append(f"{section}: rendered {seen} of {expected} diagnostic item(s)")
        section = None
        expected = None
        seen = 0

    for line in text.splitlines():
        clean = line.strip()
        lowered = line.lower()
        locked_header = re.match(
            r"^\[!\]\s+version-locked plugins\s+\((\d+)\)", clean,
            flags=re.IGNORECASE,
        )
        contested_header = re.match(
            r"^contested dlls\s+.*\((\d+)\):$", clean,
            flags=re.IGNORECASE,
        )
        if locked_header:
            finish_section()
            section = "version-locked"
            expected = int(locked_header.group(1))
            continue
        if contested_header:
            finish_section()
            section = "contested"
            expected = int(contested_header.group(1))
            continue
        if not clean:
            finish_section()
            continue
        if section and clean.startswith("- "):
            diagnostics.append(f"{section}: {clean[2:]}")
            seen += 1
            continue
        if section and clean.startswith("... [showing "):
            incomplete.append(f"{section}: {clean}")
            continue
        # Retain compatibility with older/compact houseCARL output while avoiding
        # aggregate lines such as `compat: 73 Address Library ... 5 version-LOCKED`.
        if (lowered.strip().startswith("contested dll:") or
                lowered.strip().startswith("version-locked dll:") or
                " locked to " in f" {lowered.strip()} "):
            diagnostics.append(clean)
    finish_section()
    return sorted(set(diagnostics)), sorted(set(incomplete))


def regex_count(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def validate_scripts_has_findings(text: str) -> bool:
    lowered = text.lower()
    counts = [
        regex_count(r"(\d+)\s+unbound", text),
        regex_count(r"(\d+)\s+bound-but-null", text),
        regex_count(r"(\d+)\s+unverifiable", text),
    ]
    if any(count is not None for count in counts):
        return any((count or 0) > 0 for count in counts)
    return any(marker in lowered for marker in ("[unbound]", "bound-but-null", "unverifiable"))


def classify_static_result(current: dict, baseline: dict | None = None) -> dict:
    tool = current.get("tool", "")
    text = current.get("text", "")
    base_text = (baseline or {}).get("text", "")
    status = "pass"
    findings: list[str] = []

    if current.get("is_error"):
        return {"status": "fail", "findings": ["houseCARL tool returned isError"]}

    if tool == "housecarl_load_order_status":
        warnings = normalize_load_order_warnings(text)
        base_warnings = normalize_load_order_warnings(base_text) if baseline else []
        new = [w for w in warnings if w not in base_warnings]
        if new:
            status = "fail"
            findings.extend(new)
    elif tool == "housecarl_check_errors":
        missing = regex_count(r"(\d+)\s+missing master", text)
        base_missing = regex_count(r"(\d+)\s+missing master", base_text) if baseline else 0
        if missing is not None and missing > (base_missing or 0):
            status = "fail"
            findings.append(f"missing masters increased: {base_missing or 0} -> {missing}")
        elif baseline and text != base_text:
            status = "warn"
            findings.append("check_errors output differs from baseline; review raw report")
        elif not baseline and missing:
            status = "fail"
            findings.append(f"missing masters: {missing}")
    elif tool == "housecarl_skse_inventory":
        diagnostics, incomplete = skse_diagnostics(text)
        base_diagnostics, _base_incomplete = skse_diagnostics(base_text) if baseline else ([], [])
        if incomplete:
            status = "fail"
            findings.extend(f"SKSE diagnostic inventory incomplete: {item}" for item in incomplete)
        elif not baseline:
            if diagnostics:
                status = "warn"
                findings.append("SKSE diagnostics present; use a baseline to distinguish existing contested/locked DLLs")
        else:
            new = [d for d in diagnostics if d not in base_diagnostics]
            if new:
                status = "fail"
                findings.extend(new)
    elif tool == "housecarl_validate_scripts":
        has_findings = validate_scripts_has_findings(text)
        base_has_findings = validate_scripts_has_findings(base_text) if baseline else False
        if has_findings and not (baseline and text == base_text):
            scoped_plugins = bool((current.get("arguments") or {}).get("plugins"))
            status = "fail" if scoped_plugins or baseline else "warn"
            if baseline and base_has_findings and text != base_text:
                findings.append("script validation output differs from baseline; review raw report")
            else:
                findings.append("static validator reported script binding findings")
    else:
        lowered = text.lower()
        bad_markers = ["[error]", "missing master", "dangling", "unbound", "unverifiable", "absent", "could not", "failed"]
        if any(marker in lowered for marker in bad_markers):
            if baseline and text == base_text:
                status = "pass"
            else:
                scoped_plugins = bool((current.get("arguments") or {}).get("plugins"))
                status = "fail" if scoped_plugins or baseline else "warn"
                findings.append("static validator reported errors or unresolved assets")

    return {"status": status, "findings": findings}


def evaluate_static_gates(capture: dict, baseline: dict | None = None) -> dict:
    if capture.get("format") != STATIC_GATE_FORMAT:
        raise Fail("not a mo2ctl static-gates capture")
    baseline_by_key = {tool_key(item): item for item in (baseline or {}).get("tools", [])}
    gate_results = []
    status_rank = {"pass": 0, "warn": 1, "fail": 2}
    overall = "pass"
    for result in capture.get("tools", []):
        key = tool_key(result)
        classified = classify_static_result(result, baseline_by_key.get(key))
        gate_results.append({"key": key, **classified})
        if status_rank[classified["status"]] > status_rank[overall]:
            overall = classified["status"]

    crash = capture.get("crash_logs") or {}
    if crash.get("new_logs"):
        overall = "fail"
        gate_results.append({
            "key": "crash_logs",
            "status": "fail",
            "findings": [f"{len(crash['new_logs'])} crash log(s) matched triage window"],
        })

    return {
        "status": overall,
        "gates": gate_results,
    }


def parse_crash_time(path: Path) -> datetime | None:
    match = re.match(r"crash-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.log$", path.name)
    if not match:
        return None
    return datetime(*map(int, match.groups()), tzinfo=UTC)


def parse_uptime_ms(text: str) -> int | None:
    patterns = [
        r"uptime[: ]+([0-9,]+)\s*ms",
        r"uptime[: ]+([0-9,]+)\s*milliseconds",
        r"Uptime\s*:\s*([0-9,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def uptime_bin(ms: int | None) -> str:
    if ms is None:
        return "unknown"
    if ms <= 8000:
        return "load/plugin-conflict window"
    if ms <= 90000:
        return "entry/initialization window"
    return "content/playtime window"


def summarize_crash_log(path: Path, mod: str | None) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    modules = []
    for match in re.finditer(r"([A-Za-z0-9_ .'-]+\.(?:dll|exe))\+([0-9A-Fa-f]+)", text, flags=re.IGNORECASE):
        module = f"{match.group(1)}+{match.group(2)}"
        if module not in modules:
            modules.append(module)
        if len(modules) == 3:
            break
    uptime = parse_uptime_ms(text)
    has_stack = "call stack" in text.lower()
    relevant = bool(mod and mod.lower() in text.lower())
    exception = ""
    for line in text.splitlines():
        lowered = line.lower()
        if "exception" in lowered or "access violation" in lowered:
            exception = line.strip()
            break
    return {
        "file": str(path),
        "time": parse_crash_time(path).isoformat() if parse_crash_time(path) else None,
        "has_call_stack": has_stack,
        "top_modules": modules,
        "mentions_mod": relevant,
        "exception": exception,
        "uptime_ms": uptime,
        "uptime_bin": uptime_bin(uptime),
        "attribution": "candidate" if relevant or any(mod and mod.lower() in m.lower() for m in modules)
                       else "unable_to_attribute" if not has_stack else "not_matched_to_mod",
    }


def crash_log_dir_from_status(text: str) -> Path | None:
    match = re.search(r"crash_logs:\s+(.+?)\s+\(", text)
    if not match:
        return None
    return Path(match.group(1)).expanduser()


def crash_triage_from_capture(results: list[dict], args) -> dict:
    if not args.crash_since:
        return {"checked": False, "reason": "no --crash-since provided"}
    status_text = next((r.get("text", "") for r in results if r.get("tool") == "housecarl_load_order_status"), "")
    folder = crash_log_dir_from_status(status_text)
    if not folder or not folder.is_dir():
        return {"checked": False, "reason": "crash log folder not configured or missing"}
    try:
        since = datetime.fromisoformat(args.crash_since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Fail(f"--crash-since must be ISO datetime, got {args.crash_since!r}") from exc
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    chosen = []
    last_time: datetime | None = None
    for path in sorted(folder.glob("crash-*.log")):
        when = parse_crash_time(path)
        if not when or when < since:
            continue
        if last_time and abs((when - last_time).total_seconds()) <= 1:
            continue
        chosen.append(summarize_crash_log(path, args.mod))
        last_time = when
    return {"checked": True, "folder": str(folder), "since": since.isoformat(), "new_logs": chosen}


BACKUP_DIR_NAME = ".mo2ctl-backups"
BACKUP_KEEP = 20


def backup_file(path: Path) -> Path:
    """Snapshot a profile file before writing it.

    Backups go in a subdirectory rather than beside the original: a QA loop
    installs and uninstalls on every run, and dropping `modlist.txt.bak-<stamp>`
    next to `modlist.txt` each time turns the profile directory into a junk
    drawer — and MO2 lists unknown files there in its UI.
    """
    dest_dir = path.parent / BACKUP_DIR_NAME
    dest_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = dest_dir / f"{path.name}.{stamp}"
    shutil.copy2(path, dest)

    old = sorted(dest_dir.glob(f"{path.name}.*"))[:-BACKUP_KEEP]
    for stale in old:
        stale.unlink(missing_ok=True)
    return dest


# ---------------------------------------------------------------------------
# Process / bridge probing
# ---------------------------------------------------------------------------


def iter_procs():
    """Yield (pid, argv) for every readable process except this one.

    Reads /proc directly rather than shelling out to pgrep/pkill. A `pkill -f`
    whose pattern matches the invoking shell's own command line kills the shell;
    that has happened here. Scanning /proc and skipping our own pid cannot.
    """
    me = os.getpid()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            raw = (Path(entry.path) / "cmdline").read_bytes()
        except OSError:
            continue
        argv = raw.decode("utf-8", "replace").split("\0")
        if argv and argv[0]:
            yield pid, argv


def runs_exe(argv: list[str], exe: str) -> bool:
    """True when argv[0] *is* this executable, rather than merely mentioning it.

    argv[0] and not "anywhere in the command line": `protontricks-launch --appid
    489830 .../ModOrganizer.exe moshortcut://:SKSE` names ModOrganizer.exe as an
    argument, and a substring match counted the launcher, its wrapper and its
    python parent as three extra copies of MO2 — so `kill --mo2` reported five
    victims and the lock would have stayed on after MO2 itself was gone.
    """
    return argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower() == exe.lower()


def game_pids() -> list[int]:
    # Matching argv[0] also keeps SkyrimSELauncher.exe out of this: the Steam /
    # Proton chain around it (reaper, pv-adverb, the redirector) outlives the
    # game and would otherwise make it look permanently running.
    return sorted(pid for pid, argv in iter_procs() if runs_exe(argv, "SkyrimSE.exe"))


def mo2_pids() -> list[int]:
    return sorted(pid for pid, argv in iter_procs() if runs_exe(argv, "ModOrganizer.exe"))


def bridge_status(timeout: float = 1.0) -> dict:
    result = bridge.ping(timeout)
    return {"reachable": bool(result.get("ok")), **result}


def profile_lock_reason() -> str | None:
    """Why profile files must not be edited right now, or None if it's safe.

    The game being up is the obvious case (usvfs has the load order mapped).
    MO2 being up is the subtler one and matters more in practice: MO2 holds the
    profile in memory and writes modlist.txt / plugins.txt back out on exit or
    profile switch. An edit made underneath a running MO2 is not conflicted —
    it is silently reverted, minutes later, with no error anywhere.
    """
    if game_pids():
        return "Skyrim is running"
    if mo2_pids():
        return "MO2 is running (it rewrites the profile from memory on exit, discarding edits made underneath it)"
    return None


# ---------------------------------------------------------------------------
# modlist.txt
#
# Line 1 is MO2's header comment and must survive. Entries are `+Name` (enabled)
# or `-Name` (disabled), and the file reads top = highest priority. Unverified
# third-party mods default to bottom so they do not silently win every file conflict.
# ---------------------------------------------------------------------------


def modlist_index(tf: TextFile, name: str) -> int | None:
    target = name.lower()
    for i, line in enumerate(tf.lines):
        if line[:1] in "+-" and line[1:].strip().lower() == target:
            return i
    return None


def modlist_entries(tf: TextFile) -> list[tuple[str, bool]]:
    out = []
    for line in tf.lines:
        if line[:1] in "+-":
            out.append((line[1:].strip(), line[0] == "+"))
    return out


def set_mod_state(env: Env, name: str, enabled: bool) -> str:
    tf = read_file(env.modlist)
    idx = modlist_index(tf, name)
    if idx is None:
        raise Fail(f"mod not in {env.profile} modlist: {name}")
    prefix = "+" if enabled else "-"
    if tf.lines[idx].startswith(prefix):
        return "unchanged"
    tf.lines[idx] = prefix + tf.lines[idx][1:]
    write_file(tf)
    return "changed"


def priority_insert_index(tf: TextFile, spec: str) -> int:
    entries = [(i, line[1:].strip()) for i, line in enumerate(tf.lines) if line[:1] in "+-"]
    if spec == "bottom":
        return (entries[-1][0] + 1) if entries else len(tf.lines)
    if spec == "top":
        return entries[0][0] if entries else len(tf.lines)

    direction, sep, anchor = spec.partition(":")
    if sep != ":" or direction not in {"before", "after"} or not anchor:
        raise Fail("priority must be bottom, top, before:<mod name>, or after:<mod name>")
    anchor_idx = modlist_index(tf, anchor)
    if anchor_idx is None:
        raise Fail(f"priority anchor not found in modlist: {anchor}")
    return anchor_idx if direction == "before" else anchor_idx + 1


def place_mod(tf: TextFile, name: str, enabled: bool, priority: str) -> str:
    prefix = "+" if enabled else "-"
    old_idx = modlist_index(tf, name)
    if old_idx is not None:
        tf.lines.pop(old_idx)
    insert_at = priority_insert_index(tf, priority)
    tf.lines.insert(insert_at, prefix + name)
    return priority


# ---------------------------------------------------------------------------
# plugins.txt / loadorder.txt
#
# plugins.txt marks active plugins with a leading `*`; loadorder.txt lists every
# known plugin bare, in order. Appending puts the new plugin last, which is where
# a mod under test wants to be: later wins.
# ---------------------------------------------------------------------------


def plugin_files(mod_dir: Path) -> list[str]:
    return sorted(
        p.name for p in mod_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PLUGIN_SUFFIXES
    )


def add_plugins(env: Env, names: list[str]) -> list[str]:
    if not names:
        return []
    added = []

    plugins = read_file(env.plugins)
    have = {ln.lstrip("*").strip().lower() for ln in plugins.lines if ln and not ln.startswith("#")}
    for name in names:
        if name.lower() in have:
            continue
        plugins.lines.append("*" + name)
        added.append(name)
    if added:
        write_file(plugins)

    order = read_file(env.loadorder)
    have = {ln.strip().lower() for ln in order.lines if ln and not ln.startswith("#")}
    changed = False
    for name in names:
        if name.lower() not in have:
            order.lines.append(name)
            changed = True
    if changed:
        write_file(order)

    return added


def remove_plugins(env: Env, names: list[str]) -> list[str]:
    if not names:
        return []
    drop = {n.lower() for n in names}
    removed = []
    for path in (env.plugins, env.loadorder):
        tf = read_file(path)
        keep = [ln for ln in tf.lines if ln.lstrip("*").strip().lower() not in drop]
        if len(keep) != len(tf.lines):
            removed.extend(n for n in names if n not in removed)
            tf.lines = keep
            write_file(tf)
    return removed


def bsa_files(mod_dir: Path) -> list[str]:
    return sorted(
        p.name for p in mod_dir.iterdir()
        if p.is_file() and p.suffix.lower() in BSA_SUFFIXES
    )


def plugin_stems(names: list[str]) -> set[str]:
    return {Path(name).stem.lower() for name in names}


def add_archives(env: Env, bsa_names: list[str], plugin_names: list[str]) -> list[str]:
    unmanaged = [name for name in bsa_names if Path(name).stem.lower() not in plugin_stems(plugin_names)]
    if not unmanaged:
        return []
    if not env.archives.is_file():
        raise Fail(f"missing profile file: {env.archives}")
    archives = read_file(env.archives)
    have = {ln.strip().lower() for ln in archives.lines if ln and not ln.startswith("#")}
    added = []
    for name in unmanaged:
        if name.lower() not in have:
            archives.lines.append(name)
            added.append(name)
    if added:
        write_file(archives)
    return added


def remove_archives(env: Env, bsa_names: list[str], plugin_names: list[str]) -> list[str]:
    unmanaged = {name.lower() for name in bsa_names
                 if Path(name).stem.lower() not in plugin_stems(plugin_names)}
    if not unmanaged or not env.archives.is_file():
        return []
    archives = read_file(env.archives)
    removed = [ln.strip() for ln in archives.lines if ln.strip().lower() in unmanaged]
    if removed:
        archives.lines = [ln for ln in archives.lines if ln.strip().lower() not in unmanaged]
        write_file(archives)
    return removed


# ---------------------------------------------------------------------------
# Archive + FOMOD resolution
# ---------------------------------------------------------------------------


@dataclass
class FileInstall:
    source: str
    target: str


@dataclass
class FomodPlugin:
    step: str
    group: str
    name: str
    type_name: str
    description: str
    files: list[FileInstall]

    @property
    def choice_id(self) -> str:
        return f"{self.step}/{self.group}/{self.name}"


@dataclass
class FomodGroup:
    step: str
    name: str
    select_type: str
    plugins: list[FomodPlugin]


@dataclass
class FomodPlan:
    name: str | None
    version: str | None
    required_files: list[FileInstall]
    groups: list[FomodGroup]
    unsupported: list[str]

    def default_choices(self) -> dict:
        selected: dict[str, dict[str, list[str]]] = {}
        handoff: list[str] = list(self.unsupported)
        for group in self.groups:
            picks = default_group_picks(group)
            if picks is None:
                handoff.append(
                    f"{group.step}/{group.name}: no deterministic default for {group.select_type}"
                )
                picks = []
            selected.setdefault(group.step, {})[group.name] = picks
        status = "handoff_user" if handoff else "ready"
        return {
            "format": FOMOD_CHOICES_FORMAT,
            "status": status,
            "mod_name": self.name,
            "version": self.version,
            "selected": selected,
            "handoff_reasons": handoff,
        }

    def summary(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "required_files": [fi.__dict__ for fi in self.required_files],
            "groups": [
                {
                    "step": group.step,
                    "name": group.name,
                    "select_type": group.select_type,
                    "plugins": [
                        {
                            "name": plugin.name,
                            "type": plugin.type_name,
                            "description": plugin.description,
                            "files": [fi.__dict__ for fi in plugin.files],
                            "choice_id": plugin.choice_id,
                        }
                        for plugin in group.plugins
                    ],
                }
                for group in self.groups
            ],
            "unsupported": self.unsupported,
            "default_choices": self.default_choices(),
        }


@dataclass
class ResolvedSource:
    source_dir: Path | None
    loose_files: list[Path]
    name: str
    version: str | None = None
    warnings: list[str] | None = None
    fomod: FomodPlan | None = None
    fomod_choices: dict | None = None
    cleanup: Path | None = None


def local_name(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if local_name(child) == name and child.text:
            return child.text.strip()
    return None


def first_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if local_name(child) == name:
            return child
    return None


def all_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if local_name(child) == name]


def safe_rel(raw: str) -> Path:
    raw = raw.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise Fail(f"unsafe archive path: {raw!r}")
    raw = raw.strip("/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise Fail(f"unsafe archive path: {raw!r}")
    return path


def extract_zip(src: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                if not info.filename or info.filename.endswith("/"):
                    continue
                rel = safe_rel(info.filename)
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as reader, out.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
    except zipfile.BadZipFile as exc:
        raise Fail(f"bad zip archive: {src}") from exc


def unpack_archive(src: Path, work: Path) -> Path:
    suffix = src.suffix.lower()
    if suffix == ".zip":
        root = work / "archive"
        root.mkdir()
        extract_zip(src, root)
        return root
    if suffix in (".7z", ".rar"):
        tool = shutil.which("7z") or shutil.which("unar")
        if not tool:
            raise Fail(
                f"handoff_user: {src.name} is {suffix}; install 7z/unar or unpack it manually first"
            )
        root = work / "archive"
        root.mkdir()
        if Path(tool).name == "unar":
            cmd = [tool, "-quiet", "-force-overwrite", "-output-directory", str(root), str(src)]
        else:
            cmd = [tool, "x", f"-o{root}", "-y", str(src)]
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            raise Fail(f"handoff_user: {src.name} could not be unpacked by {Path(tool).name}: {proc.stdout.strip()}")
        return root
    raise Fail(f"source file is not a plugin or supported archive: {src.name}")


def find_case_insensitive(root: Path, rel: str) -> Path | None:
    current = root
    for part in safe_rel(rel).parts:
        try:
            matches = [p for p in current.iterdir() if p.name.lower() == part.lower()]
        except FileNotFoundError:
            return None
        if not matches:
            return None
        current = matches[0]
    return current


def candidate_mod_roots(root: Path) -> list[Path]:
    candidates = []
    for path in [root, *(p for p in root.rglob("*") if p.is_dir())]:
        if looks_like_mod_root(path) or find_case_insensitive(path, "fomod/ModuleConfig.xml"):
            candidates.append(path)
    candidates.sort(key=lambda p: (len(p.relative_to(root).parts), str(p).lower()))
    return candidates


def choose_mod_root(root: Path) -> Path:
    children = [p for p in root.iterdir() if not p.name.startswith(".")]
    if (len(children) == 1 and children[0].is_dir()
            and children[0].name.lower() not in {"data", "fomod"}):
        root = children[0]
    if find_case_insensitive(root, "fomod/ModuleConfig.xml"):
        return root
    data = find_case_insensitive(root, "Data")
    if data and looks_like_mod_root(data):
        return data
    candidates = candidate_mod_roots(root)
    if not candidates:
        return root
    return candidates[0]


def file_installs(parent: ET.Element) -> list[FileInstall]:
    installs: list[FileInstall] = []
    for elem in parent.iter():
        tag = local_name(elem)
        if tag not in {"file", "folder"}:
            continue
        source = elem.get("source")
        if not source:
            continue
        target = elem.get("destination") or elem.get("target") or ""
        installs.append(FileInstall(str(safe_rel(source)), str(safe_rel(target)) if target else ""))
    return installs


def plugin_type(plugin: ET.Element, unsupported: list[str], label: str) -> str:
    descriptor = first_child(plugin, "typeDescriptor")
    if descriptor is None:
        return "Optional"
    dependency = first_child(descriptor, "dependencyType")
    if dependency is not None:
        unsupported.append(f"{label}: dependencyType requires runtime condition evaluation")
        return "Conditional"
    type_elem = first_child(descriptor, "type")
    return type_elem.get("name", "Optional") if type_elem is not None else "Optional"


def parse_info_xml(root: Path) -> tuple[str | None, str | None]:
    info = find_case_insensitive(root, "fomod/info.xml")
    if not info:
        return None, None
    try:
        doc = ET.parse(info).getroot()
    except ET.ParseError as exc:
        raise Fail(f"handoff_user: cannot parse fomod/info.xml: {exc}") from exc
    return child_text(doc, "Name"), child_text(doc, "Version")


def parse_fomod(root: Path) -> FomodPlan | None:
    config = find_case_insensitive(root, "fomod/ModuleConfig.xml")
    if not config:
        return None
    try:
        doc = ET.parse(config).getroot()
    except ET.ParseError as exc:
        raise Fail(f"handoff_user: cannot parse fomod/ModuleConfig.xml: {exc}") from exc

    name, version = parse_info_xml(root)
    name = name or doc.get("moduleName") or child_text(doc, "moduleName")
    unsupported: list[str] = []

    required_files: list[FileInstall] = []
    install_steps = first_child(doc, "installSteps")
    required = first_child(doc, "requiredInstallFiles")
    if required is not None:
        required_files.extend(file_installs(required))

    conditional = first_child(doc, "conditionalFileInstalls")
    if conditional is not None and list(conditional):
        unsupported.append("conditionalFileInstalls requires runtime flag evaluation")

    groups: list[FomodGroup] = []
    if install_steps is not None:
        for step in all_children(install_steps, "installStep"):
            step_name = step.get("name") or "Install"
            visible = first_child(step, "visible")
            if visible is not None and list(visible):
                unsupported.append(f"{step_name}: visible conditions are not supported")
            optional_file_groups = first_child(step, "optionalFileGroups")
            if optional_file_groups is None:
                continue
            for group in all_children(optional_file_groups, "group"):
                group_name = group.get("name") or "Options"
                select_type = group.get("type") or "SelectAny"
                plugins_elem = first_child(group, "plugins")
                plugins: list[FomodPlugin] = []
                if plugins_elem is not None:
                    for plugin in all_children(plugins_elem, "plugin"):
                        plugin_name = plugin.get("name") or "Option"
                        label = f"{step_name}/{group_name}/{plugin_name}"
                        files_elem = first_child(plugin, "files")
                        plugins.append(FomodPlugin(
                            step=step_name,
                            group=group_name,
                            name=plugin_name,
                            type_name=plugin_type(plugin, unsupported, label),
                            description=child_text(plugin, "description") or "",
                            files=file_installs(files_elem) if files_elem is not None else [],
                        ))
                groups.append(FomodGroup(step=step_name, name=group_name,
                                         select_type=select_type, plugins=plugins))

    return FomodPlan(name=name, version=version, required_files=required_files,
                     groups=groups, unsupported=unsupported)


def default_group_picks(group: FomodGroup) -> list[str] | None:
    required = [p.name for p in group.plugins if p.type_name.lower() == "required"]
    recommended = [p.name for p in group.plugins if p.type_name.lower() == "recommended"]
    selected = required + recommended
    if group.select_type == "SelectAny":
        return selected
    if group.select_type == "SelectAtLeastOne":
        return selected if selected else None
    if group.select_type == "SelectExactlyOne":
        if len(required) == 1:
            return required
        if not required and len(recommended) == 1:
            return recommended
        return None
    if group.select_type == "SelectAtMostOne":
        if len(selected) <= 1:
            return selected
        return None
    return None


def load_fomod_choices(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FOMOD_CHOICES_FORMAT:
        raise Fail(f"{path} is not a {FOMOD_CHOICES_FORMAT} file")
    if data.get("status") == "handoff_user":
        reasons = "; ".join(data.get("handoff_reasons") or ["manual choices required"])
        raise Fail(f"handoff_user: choices file is not replayable: {reasons}")
    return data


def selected_plugin_names(choices: dict, step: str, group: str) -> set[str]:
    selected = choices.get("selected", {})
    return set(selected.get(step, {}).get(group, []))


def copy_install(root: Path, install: FileInstall, dest: Path) -> None:
    src = root / safe_rel(install.source)
    if not src.exists():
        raise Fail(f"handoff_user: FOMOD references missing source: {install.source}")
    if src.is_dir():
        out = dest / safe_rel(install.target) if install.target else dest
        if out.exists() and not out.is_dir():
            raise Fail(f"cannot merge folder over file: {install.target}")
        shutil.copytree(src, out, dirs_exist_ok=True)
    else:
        target_rel = safe_rel(install.target) if install.target else Path(src.name)
        out = dest / target_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


def materialize_fomod(root: Path, plan: FomodPlan, choices: dict, dest: Path) -> None:
    if plan.unsupported:
        raise Fail(f"handoff_user: unsupported FOMOD features: {'; '.join(plan.unsupported)}")
    dest.mkdir(parents=True)
    for install in plan.required_files:
        copy_install(root, install, dest)
    known = {(group.step, group.name, plugin.name)
             for group in plan.groups for plugin in group.plugins}
    requested = []
    for step, groups in choices.get("selected", {}).items():
        for group, plugins in groups.items():
            for plugin in plugins:
                requested.append((step, group, plugin))
    unknown = ["/".join(item) for item in requested if item not in known]
    if unknown:
        raise Fail(f"choices reference unknown FOMOD plugin(s): {', '.join(unknown)}")
    for group in plan.groups:
        selected = selected_plugin_names(choices, group.step, group.name)
        for plugin in group.plugins:
            if plugin.name in selected:
                for install in plugin.files:
                    copy_install(root, install, dest)


def inspect_source(src: Path) -> dict:
    src = src.expanduser().resolve()
    if not src.exists():
        raise Fail(f"source not found: {src}")
    with tempfile.TemporaryDirectory(prefix="mo2ctl-inspect-") as tmp:
        root = Path(tmp)
        source_root = unpack_archive(src, root) if src.is_file() and src.suffix.lower() in ARCHIVE_SUFFIXES else src
        mod_root = choose_mod_root(source_root) if source_root.is_dir() else source_root
        fomod = parse_fomod(mod_root) if mod_root.is_dir() else None
        return {
            "source": str(src),
            "source_type": src.suffix.lower().lstrip(".") if src.is_file() else "directory",
            "mod_root": str(mod_root),
            "looks_like_mod_root": mod_root.is_dir() and looks_like_mod_root(mod_root),
            "has_fomod": fomod is not None,
            "fomod": fomod.summary() if fomod else None,
        }


def resolve_source(src: Path, name: str | None, choices_path: Path | None = None) -> ResolvedSource:
    """Work out what to copy and what to call it.

    A bare .esp is accepted as a source, because that is exactly what ModForge
    writes into `out/`. Archives are unpacked into a temporary staging directory
    and then resolved to either a Data-level root or a materialized FOMOD result.
    """
    src = src.expanduser().resolve()
    if not src.exists():
        raise Fail(f"source not found: {src}")

    if src.is_file() and src.suffix.lower() in PLUGIN_SUFFIXES:
        return ResolvedSource(None, [src], name or src.stem)

    warnings: list[str] = []
    if src.is_file():
        work = Path(tempfile.mkdtemp(prefix="mo2ctl-install-"))
        source_root = unpack_archive(src, work)
        base_name = src.stem
    else:
        work = None
        source_root = src
        base_name = src.name

    mod_root = choose_mod_root(source_root)
    fomod = parse_fomod(mod_root)
    if fomod:
        if choices_path is None:
            choices = fomod.default_choices()
            if choices.get("status") == "handoff_user":
                reasons = "; ".join(choices.get("handoff_reasons") or [])
                raise Fail(f"handoff_user: FOMOD choices required for {src.name}: {reasons}")
        else:
            choices = load_fomod_choices(choices_path.expanduser())
        material_work = work or Path(tempfile.mkdtemp(prefix="mo2ctl-fomod-"))
        staging = material_work / "materialized"
        materialize_fomod(mod_root, fomod, choices, staging)
        if not any(staging.iterdir()):
            raise Fail("handoff_user: FOMOD choices materialized an empty mod")
        return ResolvedSource(staging, [], name or fomod.name or base_name,
                              fomod.version, warnings, fomod, choices, material_work)

    children = [p for p in mod_root.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and children[0].name.lower() == "data":
        return ResolvedSource(children[0], [], name or base_name, warnings=warnings, cleanup=work)

    return ResolvedSource(mod_root, [], name or base_name, warnings=warnings, cleanup=work)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_status(env: Env, args) -> dict:
    tf = read_file(env.modlist)
    entries = modlist_entries(tf)
    game = game_pids()
    mo2 = mo2_pids()

    info = {
        "mo2_root": str(env.root),
        "profile": env.profile,
        "game_running": bool(game),
        "game_pids": game,
        "mo2_running": bool(mo2),
        "mo2_pids": mo2,
        "bridge": bridge_status(),
        "mods_total": len(entries),
        "mods_enabled": sum(1 for _, on in entries if on),
        "mods_on_disk": sum(1 for p in env.mods.iterdir() if p.is_dir()) if env.mods.is_dir() else 0,
        "profile_writable": profile_lock_reason() is None,
        "profile_lock_reason": profile_lock_reason(),
    }
    if args.mod:
        idx = modlist_index(tf, args.mod)
        info["mod"] = {
            "name": args.mod,
            "installed": (env.mods / args.mod).is_dir(),
            "in_modlist": idx is not None,
            "enabled": idx is not None and tf.lines[idx].startswith("+"),
            "priority_from_top": idx,
        }
    return info


def cmd_inspect(env: Env, args) -> dict:
    result = inspect_source(Path(args.source))
    if args.write_choices:
        fomod = result.get("fomod")
        if not fomod:
            raise Fail("source has no FOMOD choices to write")
        out = Path(args.write_choices).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fomod["default_choices"], indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        result["choices_written"] = str(out)
    return result


def looks_like_mod_root(path: Path) -> bool:
    for child in path.iterdir():
        if child.is_dir() and child.name.lower() in DATA_DIR_NAMES:
            return True
        if child.is_file() and child.suffix.lower() in (*PLUGIN_SUFFIXES, ".bsa", ".ini"):
            return True
    return False


def cmd_install(env: Env, args) -> dict:
    require_writable(args)

    resolved = resolve_source(
        Path(args.source),
        args.name,
        Path(args.fomod_choices) if getattr(args, "fomod_choices", None) else None,
    )
    src_dir, loose, name = resolved.source_dir, resolved.loose_files, resolved.name
    dest = env.mods / name

    try:
        if dest.exists():
            if not args.force:
                raise Fail(f"mod folder already exists: {dest} (use --force to replace)")
            shutil.rmtree(dest)

        warnings = list(resolved.warnings or [])
        if src_dir is not None and not looks_like_mod_root(src_dir):
            warnings.append(
                f"{src_dir} has no recognisable Data-level content (no plugin, bsa, or "
                f"known subdirectory) — MO2 will mount it but the game may see nothing"
            )

        if src_dir is not None:
            shutil.copytree(src_dir, dest)
        else:
            dest.mkdir(parents=True)
            for f in loose:
                shutil.copy2(f, dest / f.name)
        if resolved.fomod_choices:
            (dest / "mo2ctl-fomod-choices.json").write_text(
                json.dumps(resolved.fomod_choices, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        write_meta_ini(dest, resolved.version or args.version, args.comment)

        tf = read_file(env.modlist)
        priority = place_mod(tf, name, not args.no_enable, getattr(args, "priority", "bottom"))
        write_file(tf)

        plugins = plugin_files(dest)
        activated = add_plugins(env, plugins) if not args.no_enable else []
        archives = bsa_files(dest)
        archives_added = add_archives(env, archives, plugins) if not args.no_enable else []
    finally:
        if resolved.cleanup:
            shutil.rmtree(resolved.cleanup, ignore_errors=True)

    result = {
        "installed": name,
        "path": str(dest),
        "enabled": not args.no_enable,
        "priority": priority,
        "plugins_found": plugins,
        "plugins_activated": activated,
        "archives_found": archives,
        "archives_added": archives_added,
        "fomod": bool(resolved.fomod),
        "fomod_choices": "mo2ctl-fomod-choices.json" if resolved.fomod_choices else None,
        "warnings": warnings,
    }
    if not getattr(args, "no_manifest", False):
        result["manifest"] = update_manifest_for_install(env, result, resolved, args)
    return result


def write_meta_ini(dest: Path, version: str, comment: str) -> None:
    (dest / "meta.ini").write_text(
        "[General]\n"
        "gameName=Skyrim Special Edition\n"
        "modid=0\n"
        f"version={version}\n"
        "category=0\n"
        f"comments={comment}\n",
        encoding="utf-8",
    )


def cmd_uninstall(env: Env, args) -> dict:
    require_writable(args)

    name = args.name
    dest = env.mods / name
    plugins = plugin_files(dest) if dest.is_dir() else []
    archives = bsa_files(dest) if dest.is_dir() else []

    tf = read_file(env.modlist)
    idx = modlist_index(tf, name)
    if idx is not None:
        tf.lines.pop(idx)
        write_file(tf)

    removed_plugins = remove_plugins(env, plugins)
    removed_archives = remove_archives(env, archives, plugins)

    removed_files = False
    if dest.is_dir() and not args.keep_files:
        shutil.rmtree(dest)
        removed_files = True

    if idx is None and not removed_files and not removed_plugins and not removed_archives:
        raise Fail(f"nothing to uninstall: {name} is not in the modlist and has no folder")

    manifest_removed = False if getattr(args, "keep_manifest", False) else remove_manifest_entry(env, name)

    return {
        "uninstalled": name,
        "removed_from_modlist": idx is not None,
        "removed_plugins": removed_plugins,
        "removed_archives": removed_archives,
        "removed_files": removed_files,
        "removed_manifest": manifest_removed,
    }


def cmd_profile_status(env: Env, args) -> dict:
    branch = git_branch(env)
    dirty = git_porcelain(env)
    manifest = read_manifest(env)
    return {
        "profile_repo": str(env.profiles_repo),
        "profile": env.profile,
        "branch": branch,
        "head": git_head(env),
        "clean": not dirty,
        "dirty": dirty,
        "manifest_mods": sorted(manifest["mods"].keys()),
        "profile_writable": profile_lock_reason() is None,
        "profile_lock_reason": profile_lock_reason(),
    }


def cmd_profile_semantics(env: Env, args) -> dict:
    current = profile_semantics(env, None)
    base = profile_semantics(env, args.ref)
    return {
        "profile": env.profile,
        "ref": args.ref,
        "equivalent": current == base,
        "diff": semantic_diff(base, current),
        "current": current if args.show else None,
    }


def cmd_profile_absorb_churn(env: Env, args) -> dict:
    require_writable(args)
    return absorb_engine_churn(env, args.message, force=args.force)


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


def cmd_select_profile(env: Env, args) -> dict:
    require_writable(args)
    target = args.profile
    if any(ch in target for ch in "\r\n()"):
        raise Fail(f"unsafe profile name: {target!r}")
    if not (env.root / "profiles" / target).is_dir():
        raise Fail(f"profile not found: {target}")

    ini = env.root / "ModOrganizer.ini"
    if not ini.is_file():
        raise Fail(f"ModOrganizer.ini not found: {ini}")
    raw = ini.read_bytes()
    lines = raw.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.split(b"=", 1)[0].strip() == b"selected_profile"]
    if len(hits) != 1:
        raise Fail(f"selected_profile must appear exactly once in {ini}; found {len(hits)}")
    idx = hits[0]
    old_line = lines[idx]
    ending = b"\r\n" if old_line.endswith(b"\r\n") else b"\n" if old_line.endswith(b"\n") else b""
    old_value = old_line[:-len(ending)] if ending else old_line
    _, _, value = old_value.partition(b"=")
    before = value.decode("utf-8", errors="replace").strip()
    new_line = f"selected_profile=@ByteArray({target})".encode("utf-8") + ending
    lines[idx] = new_line
    ini.write_bytes(b"".join(lines))

    verify = ini.read_bytes()
    needle = f"selected_profile=@ByteArray({target})".encode("utf-8")
    if verify.count(needle) != 1:
        raise Fail(f"profile switch verification failed in {ini}")
    return {
        "ini": str(ini),
        "before": before,
        "after": f"@ByteArray({target})",
        "eol": "CRLF" if ending == b"\r\n" else "LF" if ending == b"\n" else "none",
    }


def cmd_try_begin(env: Env, args) -> dict:
    require_writable(args)
    absorbed = absorb_engine_churn(env, getattr(args, "absorb_message", None), force=False)
    require_clean_profile_repo(env)
    branch = args.branch or f"try/{slug_branch(args.name)}"
    if not branch.startswith("try/") and not args.force:
        raise Fail("try branch must start with try/ (use --force to override)")
    git_profiles(env, ["checkout", "-b", branch])
    return {
        "started": branch,
        "base": PROFILE_MAIN_BRANCH,
        "head": git_head(env),
        "absorbed_churn": absorbed,
    }


def mods_added_since_main(env: Env) -> list[str]:
    current = read_manifest(env).get("mods", {})
    base = manifest_from_git(env, PROFILE_MAIN_BRANCH).get("mods", {})
    return sorted(name for name in current.keys() if name not in base)


def cmd_try_fail(env: Env, args) -> dict:
    require_writable(args)
    branch = git_branch(env)
    if not branch.startswith("try/") and not args.force:
        raise Fail(f"refusing to fail non-try branch: {branch} (use --force to override)")

    uninstall_names = sorted(set(mods_added_since_main(env)) | set(args.uninstall or []))
    uninstall_results = []
    for name in uninstall_names:
        try:
            uninstall_args = argparse.Namespace(name=name, keep_files=False, keep_manifest=False,
                                                force=args.force)
            uninstall_results.append(cmd_uninstall(env, uninstall_args))
        except Fail as exc:
            uninstall_results.append({"uninstalled": name, "error": str(exc)})

    git_profiles(env, ["restore", "--worktree", "--staged", "."])
    git_profiles(env, ["clean", "-fd"])
    git_profiles(env, ["checkout", PROFILE_MAIN_BRANCH])
    if branch != PROFILE_MAIN_BRANCH:
        git_profiles(env, ["branch", "-D", branch])

    return {
        "failed": branch,
        "checked_out": PROFILE_MAIN_BRANCH,
        "deleted_branch": branch if branch != PROFILE_MAIN_BRANCH else None,
        "uninstalled": uninstall_results,
        "head": git_head(env),
    }


def cmd_try_pass(env: Env, args) -> dict:
    require_writable(args)
    branch = git_branch(env)
    if not branch.startswith("try/") and not args.force:
        raise Fail(f"refusing to pass non-try branch: {branch} (use --force to override)")

    dirty = git_porcelain(env)
    committed = None
    if dirty:
        git_profiles(env, ["add", "."])
        msg = args.message or f"Validate {branch.removeprefix('try/')}"
        git_profiles(env, ["commit", "-m", msg])
        committed = git_head(env)

    git_profiles(env, ["checkout", PROFILE_MAIN_BRANCH])
    git_profiles(env, ["merge", "--ff-only", branch])
    if args.delete_branch and branch != PROFILE_MAIN_BRANCH:
        git_profiles(env, ["branch", "-D", branch])

    return {
        "passed": branch,
        "checked_out": PROFILE_MAIN_BRANCH,
        "committed": committed,
        "merged_head": git_head(env),
        "deleted_branch": branch if args.delete_branch and branch != PROFILE_MAIN_BRANCH else None,
    }


def cmd_enable(env: Env, args) -> dict:
    require_writable(args)
    result = set_mod_state(env, args.name, True)
    plugins = plugin_files(env.mods / args.name) if (env.mods / args.name).is_dir() else []
    return {"mod": args.name, "enabled": True, "modlist": result,
            "plugins_activated": add_plugins(env, plugins)}


def cmd_disable(env: Env, args) -> dict:
    require_writable(args)
    result = set_mod_state(env, args.name, False)
    plugins = plugin_files(env.mods / args.name) if (env.mods / args.name).is_dir() else []
    return {"mod": args.name, "enabled": False, "modlist": result,
            "plugins_deactivated": remove_plugins(env, plugins)}


def cmd_launch(env: Env, args) -> dict:
    if game_pids():
        raise Fail("Skyrim is already running (mo2ctl kill first)")

    background_active = None
    if getattr(args, "background_active", False):
        if mo2_pids():
            raise Fail("MO2 is already running (mo2ctl kill --mo2 first)")
        background_active = enable_background_active(env)

    # protontricks-launch runs the exe inside app 489830's Proton prefix, which is
    # where MO2 itself lives — usvfs needs MO2 and the game in one wine session.
    # `moshortcut://:SKSE` is MO2's own name for the customExecutables entry, so
    # this is the same path the GUI's Run button takes.
    cmd = [
        "protontricks-launch", "--appid", STEAM_APPID,
        str(env.mo2_exe), f"moshortcut://:{args.shortcut}",
    ]
    log_path = Path(os.environ.get("MO2CTL_LOG_DIR", "/tmp")) / "mo2ctl-launch.log"
    try:
        with open(log_path, "ab") as log:
            log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd)}\n".encode())
            proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
    except Exception:
        if background_active:
            restore_background_active(env)
        raise

    result = {"launched": True, "pid": proc.pid, "shortcut": args.shortcut, "log": str(log_path)}
    if background_active:
        result["background_active"] = background_active
    if args.no_wait:
        return result

    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        status = bridge_status()
        if status.get("reachable"):
            result["bridge"] = status
            result["waited_seconds"] = round(args.wait - (deadline - time.monotonic()), 1)
            return result
        if proc.poll() is not None and not game_pids():
            result["bridge"] = {"reachable": False,
                                "error": f"launcher exited with {proc.returncode} before the bridge came up"}
            if background_active:
                result["background_active_restore"] = restore_background_active(env)
            return result
        time.sleep(2)

    result["bridge"] = {"reachable": False, "error": f"no /ping within {args.wait}s"}
    if background_active and not game_pids() and not mo2_pids():
        result["background_active_restore"] = restore_background_active(env)
    return result


def cmd_kill(env: Env, args) -> dict:
    targets = list(game_pids())
    if args.mo2:
        targets += mo2_pids()
    if not targets:
        return {"killed": [], "note": "nothing to kill",
                "background_active": restore_background_active(env)}

    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not [p for p in targets if Path(f"/proc/{p}").exists()]:
            return {"killed": targets, "escalated": [],
                    "background_active": restore_background_active(env)}
        time.sleep(0.5)

    escalated = []
    for pid in targets:
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
                escalated.append(pid)
            except ProcessLookupError:
                pass
    return {"killed": targets, "escalated": escalated,
            "background_active": restore_background_active(env)}


def require_writable(args) -> None:
    reason = profile_lock_reason()
    if reason and not args.force:
        raise Fail(f"refusing to edit the profile: {reason}. "
                   f"Run `mo2ctl kill --mo2`, or pass --force if you know better.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # --json is accepted on either side of the subcommand. SUPPRESS is what makes
    # that work: without it the subparser's own default would overwrite a --json
    # already parsed at the top level.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    p = argparse.ArgumentParser(prog="mo2ctl", description=__doc__.splitlines()[0],
                                parents=[common])
    subparsers = p.add_subparsers(dest="command", required=True)

    def sub_add(name: str, help: str) -> argparse.ArgumentParser:
        return subparsers.add_parser(name, help=help, parents=[common])

    s = sub_add("status", "what is running and whether the profile is safe to edit")
    s.add_argument("--mod", help="also report on one mod by name")
    s.set_defaults(func=cmd_status)

    s = sub_add("inspect", "inspect an archive or folder and emit replayable FOMOD choices")
    s.add_argument("source", help="mod archive or folder")
    s.add_argument("--write-choices", help="write default FOMOD choices JSON to this path")
    s.set_defaults(func=cmd_inspect, needs_env=False)

    s = sub_add("install", "copy a mod archive, folder, or bare .esp into MO2 and enable it")
    s.add_argument("source", help="mod archive, mod folder, a folder containing Data/, or a single plugin file")
    s.add_argument("--name", help="mod folder name in MO2 (default: source basename)")
    s.add_argument("--no-enable", action="store_true", help="install but leave it off")
    s.add_argument("--force", action="store_true", help="replace an existing folder / ignore the running-process lock")
    s.add_argument("--priority", default="bottom",
                   help="modlist placement: bottom, top, before:<mod>, or after:<mod> (default: bottom)")
    s.add_argument("--fomod-choices", help="replay choices JSON written by `mo2ctl inspect --write-choices`")
    s.add_argument("--version", default="0.0.0")
    s.add_argument("--source-url", help="where the archive came from, for manifest.json")
    s.add_argument("--no-manifest", action="store_true", help="do not update profiles/manifest.json")
    s.add_argument("--comment", default="Installed by mo2ctl (AI QA loop). TEST HARNESS — safe to remove.")
    s.set_defaults(func=cmd_install)

    s = sub_add("uninstall", "remove a mod from the profile and delete its folder")
    s.add_argument("name")
    s.add_argument("--keep-files", action="store_true", help="deregister but leave mods/<name> on disk")
    s.add_argument("--keep-manifest", action="store_true", help="leave profiles/manifest.json untouched")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_uninstall)

    s = sub_add("profile-status", "report profile git branch, cleanliness, and manifest contents")
    s.set_defaults(func=cmd_profile_status)

    s = sub_add("profile-semantics", "compare current profile state to a git ref by load-order semantics")
    s.add_argument("--ref", default="HEAD", help="profile repo ref to compare against (default: HEAD)")
    s.add_argument("--show", action="store_true", help="include the current semantic snapshot")
    s.set_defaults(func=cmd_profile_semantics)

    s = sub_add("profile-absorb-churn", "commit known MO2 engine churn when profile semantics did not change")
    s.add_argument("-m", "--message", help="profile repo commit message")
    s.add_argument("--force", action="store_true", help="commit despite semantic or path differences")
    s.set_defaults(func=cmd_profile_absorb_churn)

    s = sub_add("static-gates", "run houseCARL static gates and emit a pass/warn/fail report")
    s.add_argument("--mod", help="mod folder/name under test; used for lookup and crash attribution")
    s.add_argument("--plugin", action="append",
                   help="plugin filename under test; repeat for small batches. Omit to sweep whole order.")
    s.add_argument("--dialogue-formid", action="append",
                   help="DIAL/QUST/DLVW/DLBR FormID to validate with housecarl_validate_dialogue")
    s.add_argument("--asset", action="append",
                   help="Data-relative asset path to resolve with housecarl_asset_status")
    s.add_argument("--mesh", action="append",
                   help="Data-relative mesh path to inspect with housecarl_nif_inspect")
    s.add_argument("--nif-sections", default="", help="nif inspect sections: shapes, paths, all, etc.")
    s.add_argument("--baseline", help="previous static-gates capture JSON to compare against")
    s.add_argument("--write-baseline", help="write the raw capture JSON here for before/after comparison")
    s.add_argument("--report", help="write full report JSON here")
    s.add_argument("--housecarl-server", help=f"houseCARL MCP server path (default: {DEFAULT_HOUSECARL_SERVER})")
    s.add_argument("--limit", type=int, default=100, help="finding cap passed to houseCARL validators")
    s.add_argument("--max-chars", type=int, default=80000, help="max chars per houseCARL tool response")
    s.add_argument("--crash-since", help="ISO timestamp; triage crash-*.log files from this time onward")
    s.set_defaults(func=cmd_static_gates)

    s = sub_add("select-profile", "switch ModOrganizer.ini selected_profile while preserving line endings")
    s.add_argument("profile")
    s.add_argument("--force", action="store_true", help="ignore the running-process lock")
    s.set_defaults(func=cmd_select_profile)

    s = sub_add("try-begin", "create and check out a clean try/<mod> profile branch")
    s.add_argument("name", help="mod or experiment name")
    s.add_argument("--branch", help="explicit branch name (default: try/<slug>)")
    s.add_argument("--absorb-message", help="commit message if known MO2 engine churn must be absorbed first")
    s.add_argument("--force", action="store_true", help="allow a branch name outside try/")
    s.set_defaults(func=cmd_try_begin)

    s = sub_add("try-fail", "abort the current try/ branch and restore profile main")
    s.add_argument("--uninstall", action="append",
                   help="extra mod folder to remove from MO2 mods/ before restoring the profile")
    s.add_argument("--force", action="store_true", help="allow aborting outside try/")
    s.set_defaults(func=cmd_try_fail)

    s = sub_add("try-pass", "commit the current try/ branch and fast-forward profile main")
    s.add_argument("-m", "--message", help="profile repo commit message")
    s.add_argument("--delete-branch", action="store_true", help="delete the try/ branch after merge")
    s.add_argument("--force", action="store_true", help="allow passing outside try/")
    s.set_defaults(func=cmd_try_pass)

    s = sub_add("enable", "turn a mod on in the profile")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_enable)

    s = sub_add("disable", "turn a mod off in the profile")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_disable)

    s = sub_add("launch", "start SKSE through MO2 inside the game's Proton prefix")
    s.add_argument("--shortcut", default="SKSE", help="MO2 customExecutables title (default: SKSE)")
    s.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the bridge (default: 180)")
    s.add_argument("--no-wait", action="store_true", help="return as soon as the launcher is spawned")
    s.add_argument("--background-active", action="store_true",
                   help="temporarily set bAlwaysActive=1; `kill` restores Skyrim.ini")
    s.set_defaults(func=cmd_launch)

    s = sub_add("kill", "terminate the game")
    s.add_argument("--mo2", action="store_true", help="close MO2 too")
    s.add_argument("--timeout", type=float, default=15.0, help="seconds before SIGKILL")
    s.set_defaults(func=cmd_kill)

    return p


def render(result: dict) -> str:
    lines = []
    for key, value in result.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(f"  {k}: {v}" for k, v in value.items())
        elif isinstance(value, list):
            lines.append(f"{key}: {', '.join(map(str, value)) if value else '-'}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    as_json = getattr(args, "json", False)  # SUPPRESS means the attribute may be absent
    try:
        env = load_env() if getattr(args, "needs_env", True) else None
        result = args.func(env, args)
    except Fail as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=1))
        else:
            print(f"mo2ctl: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps({"ok": True, **result}, indent=1, ensure_ascii=False))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
