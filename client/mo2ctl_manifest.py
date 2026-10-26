"""Provenance ledger 與唯讀 checkpoint 快照。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    ARCHIVE_SUFFIXES, Env, Fail, PROFILE_MANIFEST_FORMAT, PROFILE_STATE_FORMAT,
)
from mo2ctl_files import (  # noqa: F401
    atomic_write, read_file, sha256_file, utc_stamp,
)
from mo2ctl_profile_lists import (  # noqa: F401
    modlist_entries,
)


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


def write_manifest(env: Env, data: dict, *, source: str) -> None:
    data["format"] = PROFILE_MANIFEST_FORMAT
    data["updated_at"] = utc_stamp()
    data["updated_by"] = source
    atomic_write(
        env.manifest,
        (json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def profile_diff(env: Env) -> dict:
    """Compare the case-sensitive enabled mod set with the provenance ledger."""
    entries = dict(modlist_entries(read_file(env.modlist)))
    modlist_names = set(entries)
    enabled = {name for name, is_enabled in entries.items() if is_enabled}
    manifest_mods = read_manifest(env)["mods"]
    manifest_names = set(manifest_mods)
    return {
        "modlist_enabled": len(enabled),
        "manifest_mods": len(manifest_names),
        "only_in_modlist": sorted(enabled - manifest_names),
        "only_in_manifest": sorted(manifest_names - modlist_names),
        "enabled_mismatch": sorted(
            name for name in manifest_names & modlist_names
            if manifest_mods[name].get("enabled") != entries[name]
        ),
    }


def _profile_file_state(path: Path) -> dict:
    tf = read_file(path)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0)
    return {
        "sha256": sha256_file(path),
        "mtime": mtime.isoformat().replace("+00:00", "Z"),
        "lines": len(tf.lines),
    }


def build_profile_state(env: Env, *, source: str,
                        manifest_dirty: str | None = None) -> dict:
    """Build a live profile checkpoint without writing it."""
    modlist = read_file(env.modlist)
    enabled = sorted(name for name, active in modlist_entries(modlist) if active)
    plugins = read_file(env.plugins)
    loadorder = read_file(env.loadorder)
    diff = profile_diff(env)
    return {
        "format": PROFILE_STATE_FORMAT,
        "profile": env.profile,
        "checkpoint_at": utc_stamp(),
        "source": source,
        "manifest_dirty": manifest_dirty,
        "manifest_sha256": sha256_file(env.manifest),
        "files": {
            "modlist": _profile_file_state(env.modlist),
            "plugins": _profile_file_state(env.plugins),
            "loadorder": _profile_file_state(env.loadorder),
        },
        "mod_order": modlist_entries(modlist),
        "enabled_count": len(enabled),
        "enabled": enabled,
        "plugin_order": [
            line[1:].strip() for line in plugins.lines if line.startswith("*")
        ],
        "load_order": [
            line.strip() for line in loadorder.lines
            if line.strip() and not line.lstrip().startswith("#")
        ],
        "unregistered": diff["only_in_modlist"],
        "stale": diff["only_in_manifest"],
        "enabled_mismatch": diff["enabled_mismatch"],
    }


def update_manifest_for_install(env: Env, manifest: dict, result: dict,
                                resolved: "ResolvedSource", args) -> dict:
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
    return entry


def remove_manifest_entry(manifest: dict, name: str) -> bool:
    if name not in manifest["mods"]:
        return False
    del manifest["mods"][name]
    return True


def archive_library_status(sha256: str | None) -> str:
    if not sha256:
        return "not_applicable"
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256.lower()):
        return "unchecked"
    exe = shutil.which("mongosh") or shutil.which("mongo")
    if not exe:
        return "unchecked"
    js = f'const n=db.getSiblingDB("skyrim").archives.countDocuments({{_id:"{sha256.lower()}"}}); print(n);'
    # 本機 mod 庫的 mongod 在 27017（與 mod-library/db/*.py 的預設一致）。
    # 這裡原本硬編 27018，連不上時被 fail-safe 吞成 "unchecked"，所以一直沒被發現。
    # mongosh 冷啟動及建立連線可能超過 0.5 秒，保留 5 秒避免把可用資料庫誤判為 unchecked。
    uri = os.environ.get("SKYRIM_MONGO_URI", "mongodb://127.0.0.1:27017").rstrip("/")
    try:
        proc = subprocess.run(
            [exe, f"{uri}/skyrim", "--quiet", "--eval", js],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unchecked"
    if proc.returncode != 0:
        return "unchecked"
    last = proc.stdout.strip().splitlines()[-1:] or [""]
    return "present" if last[0].strip() not in {"", "0"} else "missing"
