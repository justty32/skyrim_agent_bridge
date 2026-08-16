#!/usr/bin/env python3
"""qa_runner — execute a qa.json and report pass/fail per step.

Phase 3.2 of the AI QA loop. Ties `mo2ctl` (install, launch, kill) to the in-game
bridge (`state`, `console`) so one file describes a whole test run and one command
executes it.

  qa_runner.py <file.qa.json> [--json] [--dry-run] [--keep-going]

Exit codes: 0 all passed, 1 something failed, 2 passed but needs a human to look,
3 the qa.json itself is wrong.

Schema: QA-SCHEMA.md.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bridge
import mo2ctl

PASS, FAIL, HANDOFF, SKIPPED = "pass", "fail", "handoff", "skipped"


class ConfigError(Exception):
    """The qa.json is wrong — caught before anything is touched."""


class StepFailed(Exception):
    def __init__(self, message: str, failures: list | None = None):
        super().__init__(message)
        self.failures = failures or []


# ---------------------------------------------------------------------------
# Baseline manifest and runtime fingerprint
# ---------------------------------------------------------------------------

BASELINE_FORMAT = "baseline-manifest-v1"
BASELINE_TRUSTED_ROOT_ENV = "QA_BASELINE_MANIFEST_ROOT"
BASELINE_EXTENSIONS = {".ess", ".skse"}
BASELINE_SIGNATURE_FIELDS = {
    "player.name",
    "player.cell_form_id",
    "player.interior",
    "player.flags.dead",
    "game.message_box.open",
}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SAVE_STEM = re.compile(r"^[A-Za-z0-9_.-]+$")


def _manifest_path(spec: dict, base_dir: Path) -> Path:
    baseline = spec.get("baseline")
    if isinstance(baseline, str):
        raise ConfigError(
            "top-level `baseline` as a save-name string is no longer sufficient; "
            "replace it with `baseline: {\"manifest\": \"/path/to/baseline-manifest.json\"}`"
        )
    if not isinstance(baseline, dict) or not baseline.get("manifest"):
        raise ConfigError(
            "load_baseline requires top-level `baseline.manifest`; unverified save loads "
            "are not allowed"
        )
    value = baseline["manifest"]
    if not isinstance(value, str):
        raise ConfigError("top-level `baseline.manifest` must be a path string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError("top-level `baseline.manifest` must be an absolute external path")
    trusted_value = os.environ.get(BASELINE_TRUSTED_ROOT_ENV)
    if not trusted_value:
        raise ConfigError(
            f"{BASELINE_TRUSTED_ROOT_ENV} must name the deployment-owned manifest root"
        )
    trusted_root = Path(trusted_value).expanduser()
    if not trusted_root.is_absolute():
        raise ConfigError(f"{BASELINE_TRUSTED_ROOT_ENV} must be absolute")
    try:
        resolved_path = path.resolve()
        resolved_root = trusted_root.resolve()
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"cannot resolve baseline manifest trust path: {exc}") from exc
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ConfigError(
            f"baseline.manifest {resolved_path} is outside trusted root {resolved_root}"
        )
    return resolved_path


def _form_id(value, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer or decimal/hex string")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            result = int(text, 16 if text.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise ConfigError(f"{label} must be an integer or decimal/hex string") from exc
    else:
        raise ConfigError(f"{label} must be an integer or decimal/hex string")
    if not 0 <= result <= 0xFFFFFFFF:
        raise ConfigError(f"{label} must fit an unsigned 32-bit FormID")
    return result


def _validate_state_signature(signature) -> dict:
    if not isinstance(signature, dict):
        raise ConfigError("baseline manifest `state_signature` must be an object")
    missing = sorted(BASELINE_SIGNATURE_FIELDS - signature.keys())
    if missing:
        raise ConfigError(
            "baseline manifest `state_signature` is missing: " + ", ".join(missing)
        )
    if not isinstance(signature["player.name"], str) or not signature["player.name"]:
        raise ConfigError("state_signature `player.name` must be a non-empty string")
    cell_form_id = _form_id(
        signature["player.cell_form_id"], "state_signature `player.cell_form_id`"
    )
    if cell_form_id == 0:
        raise ConfigError("state_signature `player.cell_form_id` must be non-zero")
    for path in ("player.interior", "player.flags.dead", "game.message_box.open"):
        if not isinstance(signature[path], bool):
            raise ConfigError(f"state_signature `{path}` must be true or false")
    if signature["game.message_box.open"] is not False:
        raise ConfigError("state_signature `game.message_box.open` must be false")
    for path in signature:
        if not isinstance(path, str) or not path:
            raise ConfigError("state_signature paths must be non-empty strings")
        if "[*]" in path:
            raise ConfigError(
                f"state_signature path {path!r} must resolve to one exact value; "
                "wildcards are not allowed"
            )
        resolve({}, path)
    return signature


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_baseline_timings(step: dict, defaults: dict) -> dict:
    values = {
        "timeout": step.get("timeout", 60.0),
        "state_timeout": step.get("state_timeout", 20.0),
        "retry_for": step.get("retry_for", defaults.get("baseline_retry_seconds", 60)),
        "retry_interval": step.get("retry_interval", 1.0),
    }
    for key, value in values.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value)):
            raise ConfigError(f"load_baseline `{key}` must be a finite number")
        if value < 0 or (key != "retry_for" and value == 0):
            qualifier = "non-negative" if key == "retry_for" else "positive"
            raise ConfigError(f"load_baseline `{key}` must be {qualifier}")
    return values


def active_baseline_context() -> dict:
    """Resolve the save directory the launched MO2 profile actually exposes."""
    try:
        env = mo2ctl.load_env()
    except (mo2ctl.Fail, OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot resolve active MO2 profile: {exc}") from exc
    try:
        selected = mo2ctl.read_selected_profile(env.root)
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read MO2 selected profile: {exc}") from exc
    if selected != env.profile:
        raise ConfigError(
            f"MO2 selected profile is {selected!r}, but qa_runner targets {env.profile!r}"
        )

    settings_path = env.profile_dir / "settings.ini"
    settings = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with settings_path.open(encoding="utf-8") as stream:
            settings.read_file(stream)
        local_saves = settings.getboolean("General", "LocalSaves", fallback=False)
        local_settings = settings.getboolean("General", "LocalSettings", fallback=False)
    except (OSError, UnicodeError, configparser.Error, ValueError) as exc:
        raise ConfigError(f"cannot read active profile settings {settings_path}: {exc}") from exc
    if not local_saves or not local_settings:
        raise ConfigError(
            f"active profile {env.profile!r} requires LocalSaves=true and LocalSettings=true; "
            "manifest pair.directory cannot be proven to be Skyrim's active save directory"
        )

    custom_path = env.profile_dir / "skyrimcustom.ini"
    custom = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with custom_path.open(encoding="utf-8") as stream:
            custom.read_file(stream)
        local_save_path = custom.get("General", "sLocalSavePath", fallback=None)
    except (OSError, UnicodeError, configparser.Error, ValueError) as exc:
        raise ConfigError(f"cannot read active profile settings {custom_path}: {exc}") from exc
    if local_save_path != "__MO_Saves\\":
        raise ConfigError(
            f"active profile {env.profile!r} requires "
            "skyrimcustom.ini [General] sLocalSavePath=__MO_Saves\\"
        )
    try:
        save_directory = (env.profile_dir / "saves").resolve()
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"cannot resolve active profile save directory: {exc}") from exc
    return {"profile": env.profile, "save_directory": save_directory}


def preflight_baseline(spec: dict, base_dir: Path, *, active_context: dict | None = None) -> dict:
    """Read and verify the exact save pair before a load command is allowed."""
    manifest_path = _manifest_path(spec, base_dir)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read baseline manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BASELINE_FORMAT:
        raise ConfigError(f"baseline manifest must use format {BASELINE_FORMAT!r}")

    profile = manifest.get("profile")
    if not isinstance(profile, str) or not profile:
        raise ConfigError("baseline manifest `profile` must be a non-empty string")
    isolation = manifest.get("isolation")
    required_isolation = {
        "settings_ini": {"LocalSaves": "true", "LocalSettings": "true"},
        "skyrimcustom_ini": {"sLocalSavePath": "__MO_Saves\\"},
    }
    if isolation != required_isolation:
        raise ConfigError(
            "baseline manifest `isolation` must require LocalSaves=true, "
            "LocalSettings=true, and sLocalSavePath=__MO_Saves\\"
        )

    pair = manifest.get("pair")
    if not isinstance(pair, dict):
        raise ConfigError("baseline manifest needs a `pair` object")
    stem = pair.get("stem")
    if not isinstance(stem, str) or not _SAVE_STEM.fullmatch(stem):
        raise ConfigError("baseline manifest `pair.stem` must be a plain save filename stem")
    directory_value = pair.get("directory")
    if not isinstance(directory_value, str) or not directory_value:
        raise ConfigError("baseline manifest `pair.directory` must be an absolute path string")
    directory = Path(directory_value).expanduser()
    if not directory.is_absolute():
        raise ConfigError("baseline manifest `pair.directory` must be absolute")
    if active_context is not None:
        active_profile = active_context.get("profile")
        active_directory = active_context.get("save_directory")
        if profile != active_profile:
            raise ConfigError(
                f"baseline manifest profile {profile!r} does not match active profile "
                f"{active_profile!r}"
            )
        try:
            resolved_directory = directory.resolve()
            resolved_active_directory = Path(active_directory).resolve()
        except (OSError, RuntimeError, TypeError) as exc:
            raise ConfigError(f"cannot resolve active save directory binding: {exc}") from exc
        if resolved_directory != resolved_active_directory:
            raise ConfigError(
                f"baseline manifest directory {directory} is not the active profile save "
                f"directory {active_directory}"
            )

    members = pair.get("members")
    if not isinstance(members, list) or len(members) != 2:
        raise ConfigError("baseline manifest must declare exactly .ess and .skse members")
    by_extension = {}
    for member in members:
        if not isinstance(member, dict):
            raise ConfigError("baseline manifest members must be objects")
        extension = member.get("extension")
        if extension in by_extension:
            raise ConfigError(f"duplicate baseline member extension: {extension!r}")
        by_extension[extension] = member
    if set(by_extension) != BASELINE_EXTENSIONS:
        raise ConfigError("baseline manifest must declare exactly .ess and .skse members")

    signature = _validate_state_signature(manifest.get("state_signature"))
    verified = []
    for extension in sorted(BASELINE_EXTENSIONS):
        member = by_extension[extension]
        expected_bytes = member.get("bytes")
        expected_sha = member.get("sha256")
        if (isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or
                expected_bytes < 0):
            raise ConfigError(f"baseline member {extension} has invalid `bytes`")
        if not isinstance(expected_sha, str) or not _SHA256.fullmatch(expected_sha):
            raise ConfigError(f"baseline member {extension} has invalid SHA-256")
        path = directory / f"{stem}{extension}"
        if not path.is_file():
            raise ConfigError(f"baseline member is missing: {path}")
        try:
            actual_bytes = path.stat().st_size
        except OSError as exc:
            raise ConfigError(f"cannot read baseline member {path}: {exc}") from exc
        if actual_bytes != expected_bytes:
            raise ConfigError(
                f"baseline member size mismatch: {path} "
                f"(expected {expected_bytes}, got {actual_bytes})"
            )
        try:
            actual_sha = _sha256_file(path)
        except OSError as exc:
            raise ConfigError(f"cannot read baseline member {path}: {exc}") from exc
        if actual_sha != expected_sha.lower():
            raise ConfigError(
                f"baseline member SHA-256 mismatch: {path} "
                f"(expected {expected_sha.lower()}, got {actual_sha})"
            )
        verified.append({
            "extension": extension,
            "path": str(path),
            "bytes": actual_bytes,
            "sha256": actual_sha,
        })

    return {
        "manifest": str(manifest_path),
        "format": BASELINE_FORMAT,
        "profile": profile,
        "stem": stem,
        "members": verified,
        "state_signature": signature,
    }


def check_state_signature(snapshot: dict, signature: dict) -> tuple[list[dict], dict]:
    """Compare exact runtime identity fields, normalising FormID representations."""
    failures = []
    actual = {}
    for path, expected in signature.items():
        values, multi = resolve(snapshot, path)
        actual[path] = values[0] if len(values) == 1 else values
        if path == "player.cell_form_id":
            expected_value = _form_id(expected, "state_signature `player.cell_form_id`")
            converted = []
            for value in values:
                try:
                    converted.append(_form_id(value, "runtime `player.cell_form_id`"))
                except ConfigError:
                    pass
            ok = expected_value in converted
        else:
            # Python considers 0 == False and 1 == True. JSON state does not:
            # a numeric modal/dead/interior flag is a malformed bridge response,
            # not a valid match for a boolean fingerprint field.
            ok = bool(values) and any(
                value == expected and
                (not isinstance(expected, bool) or isinstance(value, bool))
                for value in values
            )
        if not ok:
            failures.append({
                "path": path,
                "op": "eq",
                "expected": expected,
                "actual": values,
                "matched_any_of": len(values) if multi else None,
            })
    return failures, actual


def state_load_epoch(snapshot: dict) -> int:
    values, _multi = resolve(snapshot, "game.load_epoch")
    if (len(values) != 1 or isinstance(values[0], bool) or
            not isinstance(values[0], int) or values[0] < 0):
        raise ConfigError(
            "runtime /state must expose one non-negative integer `game.load_epoch`; "
            "rebuild and deploy the matching AgentBridge DLL"
        )
    return values[0]


def poll_state_signature(probe, signature: dict, timeout: float, interval: float,
                         after_load_epoch: int,
                         *, clock=time.monotonic, sleeper=time.sleep) -> dict:
    """Poll structured state until the complete fingerprint converges or times out."""
    deadline = clock() + max(0, timeout)
    attempts = 0
    failures = []
    actual = {}
    error = "/state was not queried"
    while True:
        attempts += 1
        snapshot = probe()
        if snapshot.get("ok"):
            failures, actual = check_state_signature(snapshot, signature)
            try:
                load_epoch = state_load_epoch(snapshot)
            except ConfigError:
                load_epoch = None
            actual["game.load_epoch"] = load_epoch
            if load_epoch is None or load_epoch <= after_load_epoch:
                failures.append({
                    "path": "game.load_epoch",
                    "op": "gt",
                    "expected": after_load_epoch,
                    "actual": [] if load_epoch is None else [load_epoch],
                    "matched_any_of": None,
                })
            if not failures:
                return {
                    "matched": True,
                    "attempts": attempts,
                    "expected": signature,
                    "actual": actual,
                    "load_epoch": {"before": after_load_epoch, "after": load_epoch},
                }
            error = f"baseline state fingerprint did not match ({len(failures)} field(s))"
        else:
            failures = []
            actual = {}
            error = f"/state unavailable: {snapshot.get('error')}"
        if clock() >= deadline:
            raise StepFailed(
                f"{error} (after {attempts} attempt(s) over {timeout}s)", failures
            )
        sleeper(interval)


# ---------------------------------------------------------------------------
# Path resolution
#
# Dotted paths into the /state JSON, with `[*]` for "every element" and `[N]` for
# one. `plugins[*].name` is the common case: it resolves to a list, and the
# comparison then asks whether ANY element satisfies it.
# ---------------------------------------------------------------------------

_SEGMENT = re.compile(r"^([^\[\]]*)((?:\[[^\[\]]+\])*)$")
_INDEX = re.compile(r"\[([^\[\]]+)\]")


def resolve(data, path: str) -> tuple[list, bool]:
    """Return (values, multi). `multi` records whether a `[*]` widened the path."""
    values = [data]
    multi = False
    for raw in path.split("."):
        match = _SEGMENT.match(raw)
        if not match:
            raise ConfigError(f"bad path segment: {raw!r} in {path!r}")
        key, indices = match.group(1), _INDEX.findall(match.group(2))

        if key:
            values = [v[key] for v in values if isinstance(v, dict) and key in v]
        for index in indices:
            widened = []
            for value in values:
                if not isinstance(value, list):
                    continue
                if index == "*":
                    widened.extend(value)
                    multi = True
                else:
                    try:
                        widened.append(value[int(index)])
                    except (ValueError, IndexError):
                        pass
            values = widened
    return values, multi


# ---------------------------------------------------------------------------
# Comparisons
#
# Positive operators pass when ANY resolved value satisfies them; negative ones
# (`ne`, `not_contains`) require ALL of them to. That is how the words read:
# "plugins[*].name not_contains Foo" means no plugin matches, not "some plugin
# doesn't".
# ---------------------------------------------------------------------------


def _contains(haystack, needle) -> bool:
    if isinstance(haystack, str):
        return str(needle) in haystack
    if isinstance(haystack, (list, tuple, dict)):
        return needle in haystack
    return False


OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "gte": lambda a, b: _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "contains": _contains,
    "not_contains": lambda a, b: not _contains(a, b),
    "matches": lambda a, b: re.search(str(b), str(a)) is not None,
}
NEGATIVE_OPS = {"ne", "not_contains"}
COUNT_OPS = {"count_eq", "count_gte", "count_lte"}
SET_OPS = {"exists"}


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(f"not comparable as a number: {value!r}")
    return float(value)


def check(data, path: str, condition) -> dict | None:
    """Evaluate one expectation. Returns a failure dict, or None when it holds."""
    if not isinstance(condition, dict):
        condition = {"eq": condition}
    if len(condition) != 1:
        raise ConfigError(f"expectation for {path!r} needs exactly one operator, got {list(condition)}")
    (op, expected), = condition.items()

    values, multi = resolve(data, path)

    if op in SET_OPS:
        ok = bool(values) == bool(expected)
    elif op in COUNT_OPS:
        count = len(values)
        ok = {"count_eq": count == expected,
              "count_gte": count >= expected,
              "count_lte": count <= expected}[op]
    elif op not in OPS:
        raise ConfigError(f"unknown operator {op!r} for {path!r}")
    elif not values:
        ok = False  # nothing there to satisfy anything, negatives included
    elif op in NEGATIVE_OPS:
        ok = all(OPS[op](v, expected) for v in values)
    else:
        ok = any(OPS[op](v, expected) for v in values)

    if ok:
        return None
    # Long paths like plugins[*].name resolve to 60 entries; a failure report that
    # dumps all of them buries the point.
    actual = values if len(values) <= 8 else values[:8] + [f"... +{len(values) - 8} more"]
    return {"path": path, "op": op, "expected": expected,
            "actual": actual, "matched_any_of": len(values) if multi else None}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _ns(**kwargs) -> SimpleNamespace:
    """mo2ctl's commands take an argparse Namespace; build one directly.

    Cheaper than shelling out per step, and it keeps mo2ctl's `Fail` messages
    intact instead of reducing them to an exit code.
    """
    return SimpleNamespace(**kwargs)


def _mo2(fn, **kwargs) -> dict:
    try:
        return fn(mo2ctl.load_env(), _ns(**kwargs))
    except mo2ctl.Fail as exc:
        raise StepFailed(str(exc)) from exc


class Runner:
    def __init__(self, spec: dict, base_dir: Path, *, interactive: bool,
                 clock=time.monotonic, sleeper=time.sleep,
                 baseline_context=active_baseline_context):
        self.spec = spec
        self.base_dir = base_dir
        self.interactive = interactive
        self.defaults = spec.get("defaults", {})
        self.clock = clock
        self.sleeper = sleeper
        self.baseline_context = baseline_context

    def path_of(self, value: str) -> Path:
        """Resolve a step's path relative to the qa.json, not the shell's cwd.

        A test file that only works when you run it from one directory is a test
        file that stops working the moment anything automates it.
        """
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    # -- individual step types ------------------------------------------------

    def step_install(self, step) -> dict:
        source = self.path_of(require(step, "source"))
        if not source.exists():
            raise StepFailed(f"source does not exist: {source}")
        return _mo2(mo2ctl.cmd_install,
                    source=str(source),
                    name=step.get("mod_name"),
                    no_enable=not step.get("enable", True),
                    force=step.get("force", True),
                    version=step.get("version", "0.0.0"),
                    comment=step.get("comment", "Installed by qa_runner. TEST HARNESS."))

    def step_uninstall(self, step) -> dict:
        return _mo2(mo2ctl.cmd_uninstall, name=require(step, "mod_name"),
                    keep_files=step.get("keep_files", False), force=step.get("force", False))

    def step_enable(self, step) -> dict:
        return _mo2(mo2ctl.cmd_enable, name=require(step, "mod_name"), force=step.get("force", False))

    def step_disable(self, step) -> dict:
        return _mo2(mo2ctl.cmd_disable, name=require(step, "mod_name"), force=step.get("force", False))

    def step_launch(self, step) -> dict:
        budget = step.get("wait", 240.0)
        started = time.time()
        result = _mo2(mo2ctl.cmd_launch, shortcut=step.get("shortcut", "SKSE"),
                      wait=budget, no_wait=False,
                      background_active=step.get("background_active", True))
        if not result.get("bridge", {}).get("reachable"):
            raise StepFailed(f"bridge never answered: {result.get('bridge', {}).get('error')}")

        # /ping answers on the socket thread and keeps answering through load
        # screens — by design, so a runner can tell "process alive, game busy"
        # from "process dead". It therefore does NOT mean the game thread is
        # draining tasks yet, and the first /state after launch reliably 503s.
        # Everything downstream asserts on state, so wait for the real thing.
        remaining = max(10.0, budget - (time.time() - started))
        snapshot = wait_for(lambda: bridge.state(timeout=10.0), remaining)
        if not snapshot.get("ok"):
            raise StepFailed(f"bridge is up but the game thread never answered "
                             f"within {remaining:.0f}s: {snapshot.get('error')}")
        result["state_ready_after_s"] = round(time.time() - started, 1)
        return result

    def step_kill(self, step) -> dict:
        return _mo2(mo2ctl.cmd_kill, mo2=step.get("mo2", False), timeout=step.get("timeout", 15.0))

    def step_load_baseline(self, step) -> dict:
        try:
            timings = _load_baseline_timings(step, self.defaults)
        except ConfigError as exc:
            raise StepFailed(f"invalid load_baseline: {exc}") from exc
        try:
            context = self.baseline_context()
            preflight = preflight_baseline(
                self.spec, self.base_dir, active_context=context
            )
        except ConfigError as exc:
            raise StepFailed(f"baseline preflight failed: {exc}") from exc
        save = preflight["stem"]
        if step.get("save") is not None and step["save"] != save:
            raise StepFailed(
                f"load_baseline `save` {step['save']!r} does not match manifest stem {save!r}"
            )
        before = bridge.state(timeout=timings["state_timeout"])
        if not before.get("ok"):
            raise StepFailed(
                f"cannot establish pre-load state epoch: {before.get('error')}"
            )
        try:
            before_epoch = state_load_epoch(before)
        except ConfigError as exc:
            raise StepFailed(f"cannot establish pre-load state epoch: {exc}") from exc
        result = bridge.console(f"load {save}", timeout=timings["timeout"])
        if not result.get("ok"):
            raise StepFailed(f"load failed: {result.get('error')}")
        # Console success only proves that the command was accepted. A save load
        # is asynchronous and may stop at a modal, so prove the complete runtime
        # identity instead of sleeping and declaring success.
        fingerprint = poll_state_signature(
            lambda: bridge.state(timeout=timings["state_timeout"]),
            preflight["state_signature"],
            timings["retry_for"],
            timings["retry_interval"],
            before_epoch,
            clock=self.clock,
            sleeper=self.sleeper,
        )
        return {
            "save": save,
            **result,
            "baseline_preflight": {
                key: value for key, value in preflight.items() if key != "state_signature"
            },
            "state_fingerprint": fingerprint,
        }

    def step_console(self, step) -> dict:
        result = bridge.console(require(step, "cmd"), step.get("ref"),
                                timeout=step.get("timeout", 30.0))
        if not result.get("ok"):
            raise StepFailed(f"console call failed: {result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_move_to_actor(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.move_to_actor(
                step.get("name"), form_id=step.get("form_id"),
                scope=step.get("scope", "cell"), distance=step.get("distance", 128.0),
                timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            raise StepFailed(f"move-to-actor failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_activate_actor(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.activate_actor(
                step.get("name"), form_id=step.get("form_id"),
                scope=step.get("scope", "cell"), timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            raise StepFailed(f"activate-actor failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_select_dialogue(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.select_dialogue(
                step.get("text"), contains=step.get("contains", False),
                index=step.get("index"), info_form_id=step.get("info_form_id"),
                timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            available = result.get("available") or []
            suffix = f"; available={available}" if available else ""
            raise StepFailed(f"select-dialogue failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}{suffix}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_close_dialogue(self, step) -> dict:
        result = bridge.close_dialogue(timeout=step.get("timeout", 20.0))
        if not result.get("ok"):
            raise StepFailed(f"close-dialogue failed: {result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_select_message_box(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.select_message_box(
                step.get("text"), index=step.get("index"), message=step.get("message"),
                timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            available = result.get("available") or []
            suffix = f"; available={available}" if available else ""
            raise StepFailed(f"select-message-box failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}{suffix}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_assert_global(self, step) -> dict:
        editor_id = require(step, "editor_id")
        condition = require(step, "expect")
        budget = step.get("retry_for", self.defaults.get("assert_retry_seconds", 20))
        interval = step.get("retry_interval", 2.0)
        deadline = time.time() + budget
        attempts = 0
        while True:
            attempts += 1
            snapshot = bridge.global_value(editor_id, timeout=step.get("timeout", 20.0))
            if snapshot.get("ok"):
                failure = check(snapshot, "value", condition)
                if not failure:
                    return {"editor_id": editor_id, "value": snapshot["value"],
                            "attempts": attempts}
                error = "global expectation failed"
            else:
                failure = None
                error = f"global unavailable: {snapshot.get('error')}"
            if time.time() >= deadline:
                raise StepFailed(f"{error} (after {attempts} attempt(s) over {budget}s)",
                                 [failure] if failure else [])
            time.sleep(interval)

    def step_wait(self, step) -> dict:
        seconds = step.get("seconds", 1)
        time.sleep(seconds)
        return {"waited": seconds}

    def step_assert_state(self, step) -> dict:
        expect = step.get("expect") or {}
        if not expect:
            raise ConfigError("assert_state needs a non-empty `expect`")

        # Assert-eventually, not assert-now. Nearly everything the game does in
        # response to a console command is asynchronous — `coc` returns before
        # the cell is loaded, an actor value takes a frame to propagate — so a
        # single-shot check turns "correct but not yet" into a failure. Retrying
        # a *passing* condition costs one request; not retrying costs a false
        # red on every timing-sensitive step. Set `retry_for: 0` to assert now.
        budget = step.get("retry_for", self.defaults.get("assert_retry_seconds", 20))
        interval = step.get("retry_interval", 2.0)
        deadline = time.time() + budget
        attempts = 0
        while True:
            attempts += 1
            snapshot = bridge.state(step.get("include"), radius=step.get("radius"),
                                    limit=step.get("limit"), timeout=step.get("timeout", 20.0))
            if snapshot.get("ok"):
                failures = [f for f in (check(snapshot, p, c) for p, c in expect.items()) if f]
                if not failures:
                    return {"checked": len(expect), "attempts": attempts}
                error = f"{len(failures)}/{len(expect)} expectation(s) failed"
            else:
                failures = []
                error = f"/state unavailable: {snapshot.get('error')}"

            if time.time() >= deadline:
                raise StepFailed(f"{error} (after {attempts} attempt(s) over {budget}s)", failures)
            time.sleep(interval)

    def step_handoff_user(self, step) -> dict:
        message = require(step, "message")
        if not self.interactive:
            # Not a terminal: whoever invoked this (an agent, CI) relays the
            # message. Per plan decision D6 the run does not try to substitute
            # for the human — it records what needs looking at and moves on,
            # and the overall status becomes needs_human.
            return {"handoff": True, "message": message, "expect": step.get("expect")}
        print(f"\n>>> {message}")
        if step.get("expect"):
            print(f"    expected: {step['expect']}")
        answer = input("    [Enter]=looks right, or type what's wrong: ").strip()
        if answer:
            raise StepFailed(f"user reported: {answer}")
        return {"handoff": True, "message": message, "confirmed_by_user": True}

    # -- driving --------------------------------------------------------------

    def run(self) -> dict:
        started = time.time()
        results: list[dict] = []
        steps = self.spec.get("steps", [])
        teardown = self.spec.get("teardown", [])

        stop_at = None
        for index, step in enumerate(steps):
            if stop_at is not None:
                results.append(self._skipped(index, step))
                continue
            outcome = self._run_one(index, step)
            results.append(outcome)
            if outcome["status"] == FAIL and not step.get("continue_on_fail"):
                stop_at = index

        # Teardown always runs. The whole point of a repeatable loop is that a
        # failure in the middle still leaves the profile the way it was found;
        # a run that fails at step 3 and leaves a test mod installed poisons
        # every run after it.
        for index, step in enumerate(teardown):
            results.append({**self._run_one(index, step), "phase": "teardown"})

        counts = {s: sum(1 for r in results if r["status"] == s) for s in (PASS, FAIL, HANDOFF, SKIPPED)}
        status = FAIL if counts[FAIL] else ("needs_human" if counts[HANDOFF] else PASS)
        return {
            "name": self.spec.get("name", "unnamed"),
            "status": status,
            "duration_s": round(time.time() - started, 1),
            "counts": counts,
            "steps": results,
            "handoffs": [r["detail"]["message"] for r in results
                         if r["status"] == HANDOFF and "message" in r.get("detail", {})],
        }

    def _run_one(self, index: int, step: dict) -> dict:
        kind = step.get("type")
        handler = getattr(self, f"step_{kind}", None)
        label = step.get("label") or describe(step)
        record = {"index": index, "type": kind, "label": label}
        if handler is None:
            return {**record, "status": FAIL, "duration_s": 0.0,
                    "error": f"unknown step type: {kind!r}"}

        started = time.time()
        try:
            detail = handler(step)
            status = HANDOFF if detail.get("handoff") and not detail.get("confirmed_by_user") else PASS
            return {**record, "status": status, "duration_s": round(time.time() - started, 1),
                    "detail": detail}
        except StepFailed as exc:
            return {**record, "status": FAIL, "duration_s": round(time.time() - started, 1),
                    "error": str(exc), "failures": exc.failures}

    @staticmethod
    def _skipped(index: int, step: dict) -> dict:
        return {"index": index, "type": step.get("type"),
                "label": step.get("label") or describe(step),
                "status": SKIPPED, "duration_s": 0.0}


def wait_for(probe, timeout: float, interval: float = 2.0) -> dict:
    """Poll `probe` until it returns a dict with ok=True, or the budget runs out."""
    deadline = time.time() + timeout
    while True:
        result = probe()
        if result.get("ok") or time.time() >= deadline:
            return result
        time.sleep(interval)


def retry_for_ok(probe, timeout: float, interval: float = 1.0) -> dict:
    """Retry a semantic action until it succeeds; always make at least one attempt."""
    deadline = time.time() + max(0, timeout)
    attempts = 0
    while True:
        attempts += 1
        result = probe()
        if result.get("ok") or time.time() >= deadline:
            return {**result, "attempts": attempts}
        time.sleep(interval)


def require(step: dict, key: str):
    if key not in step:
        raise ConfigError(f"step {step.get('type')!r} needs `{key}`")
    return step[key]


def describe(step: dict) -> str:
    kind = step.get("type", "?")
    for key in ("cmd", "name", "form_id", "message", "text", "info_form_id", "index", "editor_id",
                "mod_name", "source", "message", "save", "seconds"):
        if key in step:
            return f"{kind}: {step[key]}"
    return kind


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

KNOWN_TYPES = {"install", "uninstall", "enable", "disable", "launch", "kill",
               "load_baseline", "console", "wait", "assert_state", "assert_global",
               "move_to_actor", "activate_actor", "select_dialogue", "close_dialogue",
               "select_message_box",
               "handoff_user"}


def validate(spec: dict, base_dir: Path) -> list[str]:
    """Catch everything checkable without touching MO2 or the game.

    Worth doing eagerly: the expensive part of a run is a game launch, and
    finding out at step 9 that step 12 has a typo wastes the whole thing.
    """
    problems = []
    if not isinstance(spec.get("steps"), list) or not spec["steps"]:
        problems.append("`steps` must be a non-empty list")
        return problems

    for phase in ("steps", "teardown"):
        for index, step in enumerate(spec.get(phase) or []):
            where = f"{phase}[{index}]"
            if not isinstance(step, dict):
                problems.append(f"{where}: not an object")
                continue
            kind = step.get("type")
            if kind not in KNOWN_TYPES:
                problems.append(f"{where}: unknown type {kind!r} (known: {', '.join(sorted(KNOWN_TYPES))})")
                continue
            if kind == "install":
                source = step.get("source")
                if not source:
                    problems.append(f"{where}: install needs `source`")
                else:
                    path = Path(source).expanduser()
                    path = path if path.is_absolute() else (base_dir / path)
                    if not path.exists():
                        problems.append(f"{where}: source not found: {path}")
            if kind in ("uninstall", "enable", "disable") and not step.get("mod_name"):
                problems.append(f"{where}: {kind} needs `mod_name`")
            if kind == "console" and not step.get("cmd"):
                problems.append(f"{where}: console needs `cmd`")
            if kind in ("move_to_actor", "activate_actor"):
                selectors = [key for key in ("name", "form_id") if step.get(key) is not None]
                if len(selectors) != 1:
                    problems.append(f"{where}: {kind} needs exactly one of `name` or `form_id`")
                if step.get("scope", "cell") not in ("cell", "loaded"):
                    problems.append(f"{where}: {kind} scope must be `cell` or `loaded`")
            if kind == "select_dialogue":
                selectors = [key for key in ("text", "index", "info_form_id")
                             if step.get(key) is not None]
                if len(selectors) != 1:
                    problems.append(f"{where}: select_dialogue needs exactly one of "
                                    "`text`, `index`, or `info_form_id`")
            if kind == "select_message_box":
                selectors = [key for key in ("text", "index") if step.get(key) is not None]
                if len(selectors) != 1:
                    problems.append(f"{where}: select_message_box needs exactly one of "
                                    "`text` or `index`")
                elif selectors[0] == "text" and not step["text"]:
                    problems.append(f"{where}: select_message_box `text` must not be empty")
                elif selectors[0] == "index" and (isinstance(step["index"], bool) or
                                                   not isinstance(step["index"], int) or
                                                   step["index"] < 0):
                    problems.append(f"{where}: select_message_box `index` must be a "
                                    "non-negative integer")
            if kind == "assert_global":
                if not step.get("editor_id"):
                    problems.append(f"{where}: assert_global needs `editor_id`")
                condition = step.get("expect")
                if not isinstance(condition, dict) or len(condition) != 1:
                    problems.append(f"{where}: assert_global needs one comparison in `expect`")
                elif next(iter(condition)) not in OPS and next(iter(condition)) not in SET_OPS:
                    problems.append(f"{where}: assert_global has unknown operator {next(iter(condition))!r}")
            if kind == "handoff_user" and not step.get("message"):
                problems.append(f"{where}: handoff_user needs `message`")
            if kind == "load_baseline":
                try:
                    _load_baseline_timings(step, spec.get("defaults", {}))
                except ConfigError as exc:
                    problems.append(f"{where}: {exc}")
                try:
                    preflight = preflight_baseline(spec, base_dir)
                    if step.get("save") is not None and step["save"] != preflight["stem"]:
                        problems.append(
                            f"{where}: `save` {step['save']!r} does not match manifest stem "
                            f"{preflight['stem']!r}"
                        )
                except ConfigError as exc:
                    problems.append(f"{where}: baseline preflight failed: {exc}")
            if kind == "assert_state":
                expect = step.get("expect")
                if not isinstance(expect, dict) or not expect:
                    problems.append(f"{where}: assert_state needs a non-empty `expect` object")
                    continue
                for path, condition in expect.items():
                    if isinstance(condition, dict):
                        if len(condition) != 1:
                            problems.append(f"{where}: {path}: needs exactly one operator")
                            continue
                        op = next(iter(condition))
                        if op not in OPS and op not in COUNT_OPS and op not in SET_OPS:
                            problems.append(f"{where}: {path}: unknown operator {op!r}")
                    try:
                        resolve({}, path)
                    except ConfigError as exc:
                        problems.append(f"{where}: {exc}")
    return problems


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

MARK = {PASS: "  ok  ", FAIL: " FAIL ", HANDOFF: " look ", SKIPPED: " skip "}


def render(report: dict) -> str:
    lines = [f"{report['name']} — {report['status'].upper()} in {report['duration_s']}s"]
    for step in report["steps"]:
        tail = f" ({step['duration_s']}s)" if step["duration_s"] else ""
        phase = " [teardown]" if step.get("phase") == "teardown" else ""
        lines.append(f"[{MARK[step['status']]}] {step['label']}{tail}{phase}")
        if step.get("error"):
            lines.append(f"          {step['error']}")
        for failure in step.get("failures", []):
            lines.append(f"          {failure['path']} {failure['op']} {failure['expected']!r}"
                         f" — actual: {failure['actual']!r}")
    if report["handoffs"]:
        lines.append("\nNeeds a human to look at:")
        lines.extend(f"  - {m}" for m in report["handoffs"])
    counts = report["counts"]
    lines.append(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, "
                 f"{counts[HANDOFF]} for review, {counts[SKIPPED]} skipped")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="qa_runner", description=__doc__.splitlines()[0])
    parser.add_argument("spec", help="path to a .qa.json")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--dry-run", action="store_true", help="validate only; touch nothing")
    parser.add_argument("--no-interactive", action="store_true",
                        help="never prompt on handoff_user (default when stdin isn't a tty)")
    parser.add_argument("--report", help="also write the JSON report here")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec).expanduser().resolve()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"qa_runner: cannot read {spec_path}: {exc}", file=sys.stderr)
        return 3

    problems = validate(spec, spec_path.parent)
    if problems:
        print(f"qa_runner: {spec_path.name} is not valid:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 3
    if args.dry_run:
        total = len(spec.get("steps", [])) + len(spec.get("teardown", []))
        print(f"{spec.get('name', spec_path.stem)}: valid, {total} step(s)")
        return 0

    interactive = sys.stdin.isatty() and not args.no_interactive
    report = Runner(spec, spec_path.parent, interactive=interactive).run()

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False) if args.json else render(report))

    return {PASS: 0, FAIL: 1, "needs_human": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
