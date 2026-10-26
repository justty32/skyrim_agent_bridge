"""安全解壓與 mod root 辨識。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import zipfile

from mo2ctl_env import (  # noqa: F401
    DATA_DIR_NAMES, Fail, PLUGIN_SUFFIXES,
)


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
    if (not looks_like_mod_root(root)
            and len(children) == 1 and children[0].is_dir()
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


def looks_like_mod_root(path: Path) -> bool:
    for child in path.iterdir():
        if child.is_dir() and child.name.lower() in DATA_DIR_NAMES:
            return True
        if child.is_file() and child.suffix.lower() in (*PLUGIN_SUFFIXES, ".bsa", ".ini"):
            return True
    return False
