"""Background-active INI 備份與還原。"""

from __future__ import annotations

from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    BACKGROUND_ACTIVE_BACKUP, Env, Fail,
)
from mo2ctl_files import (  # noqa: F401
    atomic_write, read_file,
)


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
