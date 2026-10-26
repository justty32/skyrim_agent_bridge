"""Active profile 綁定與 save pair preflight。"""

from __future__ import annotations

import configparser
import json
from pathlib import Path
import mo2ctl

from qa_runner_baseline_rules import (  # noqa: F401
    BASELINE_EXTENSIONS, BASELINE_FORMAT, _SAVE_STEM, _SHA256, _manifest_path, _sha256_file,
    _validate_state_signature,
)
from qa_runner_common import (  # noqa: F401
    ConfigError,
)


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
