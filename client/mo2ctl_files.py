"""Profile 文字檔、原子寫入、雜湊與備份。"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    Fail,
)


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


@dataclass
class ProfileEdit:
    """Stage profile text and provenance until commit_profile publishes them."""

    manifest: dict | None = None
    manifest_skipped: str | None = None
    files: dict[Path, TextFile] = field(default_factory=dict)

    def read(self, path: Path) -> TextFile:
        if path not in self.files:
            self.files[path] = read_file(path)
        return self.files[path]


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
