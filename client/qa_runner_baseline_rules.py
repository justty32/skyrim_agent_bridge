"""Baseline manifest 路徑、指紋與 timing 規則。"""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path

from qa_runner_common import (  # noqa: F401
    ConfigError,
)
from qa_runner_conditions import (  # noqa: F401
    resolve,
)


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
