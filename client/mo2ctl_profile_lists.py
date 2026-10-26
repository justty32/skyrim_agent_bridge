"""Mod、plugin 與 archive 清單編輯。"""

from __future__ import annotations

from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    BSA_SUFFIXES, Env, Fail, PLUGIN_SUFFIXES,
)
from mo2ctl_files import (  # noqa: F401
    ProfileEdit, TextFile, read_file,
)


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


def set_mod_state(env: Env, name: str, enabled: bool, edit: ProfileEdit) -> str:
    tf = edit.read(env.modlist)
    idx = modlist_index(tf, name)
    if idx is None:
        raise Fail(f"mod not in {env.profile} modlist: {name}")
    prefix = "+" if enabled else "-"
    if tf.lines[idx].startswith(prefix):
        return "unchanged"
    tf.lines[idx] = prefix + tf.lines[idx][1:]
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


# ---------------------------------------------------------------------------
# plugins.txt / loadorder.txt
#
# plugins.txt marks active plugins with a leading `*`; loadorder.txt lists every
# known plugin bare, in order. New plugins append unless an install/enable call
# supplies an anchor placement.
# ---------------------------------------------------------------------------


def plugin_files(mod_dir: Path) -> list[str]:
    return sorted(
        p.name for p in mod_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PLUGIN_SUFFIXES
    )


def plugin_line_index(tf: TextFile, name: str) -> int | None:
    target = name.lower()
    for i, line in enumerate(tf.lines):
        if line and not line.startswith("#") and line.lstrip("*").strip().lower() == target:
            return i
    return None


def plugin_insert_index(
    tf: TextFile,
    *,
    after: str | None,
    before: str | None,
) -> int:
    anchor = after or before
    if anchor is None:
        return len(tf.lines)
    idx = plugin_line_index(tf, anchor)
    if idx is None:
        raise Fail(f"plugin anchor not found in {tf.path.name}: {anchor}")
    return idx + 1 if after else idx


def priority_plugin_anchor(env: Env, priority: str) -> tuple[str | None, str | None]:
    direction, sep, mod_name = priority.partition(":")
    if sep != ":" or direction not in {"after", "before"}:
        return None, None
    mod_dir = env.mods / mod_name
    if not mod_dir.is_dir():
        return None, None
    mod_plugins = {name.lower() for name in plugin_files(mod_dir)}
    if not mod_plugins:
        return None, None
    profile_plugins = read_file(env.plugins)
    matches = [
        line.lstrip("*").strip()
        for line in profile_plugins.lines
        if line and not line.startswith("#") and line.lstrip("*").strip().lower() in mod_plugins
    ]
    if not matches:
        return None, None
    if direction == "after":
        return matches[-1], None
    return None, matches[0]


def remove_plugins(env: Env, names: list[str], edit: ProfileEdit) -> list[str]:
    if not names:
        return []
    drop = {n.lower() for n in names}
    removed = []
    for path in (env.plugins, env.loadorder):
        tf = edit.read(path)
        keep = [ln for ln in tf.lines if ln.lstrip("*").strip().lower() not in drop]
        if len(keep) != len(tf.lines):
            removed.extend(n for n in names if n not in removed)
            tf.lines = keep
    return removed


def bsa_files(mod_dir: Path) -> list[str]:
    return sorted(
        p.name for p in mod_dir.iterdir()
        if p.is_file() and p.suffix.lower() in BSA_SUFFIXES
    )


def plugin_stems(names: list[str]) -> set[str]:
    return {Path(name).stem.lower() for name in names}


def add_archives(env: Env, bsa_names: list[str], plugin_names: list[str], edit: ProfileEdit) -> list[str]:
    unmanaged = [name for name in bsa_names if Path(name).stem.lower() not in plugin_stems(plugin_names)]
    if not unmanaged:
        return []
    if not env.archives.is_file():
        raise Fail(f"missing profile file: {env.archives}")
    archives = edit.read(env.archives)
    have = {ln.strip().lower() for ln in archives.lines if ln and not ln.startswith("#")}
    added = []
    for name in unmanaged:
        if name.lower() not in have:
            archives.lines.append(name)
            added.append(name)
    return added


def remove_archives(env: Env, bsa_names: list[str], plugin_names: list[str], edit: ProfileEdit) -> list[str]:
    unmanaged = {name.lower() for name in bsa_names
                 if Path(name).stem.lower() not in plugin_stems(plugin_names)}
    if not unmanaged or not env.archives.is_file():
        return []
    archives = edit.read(env.archives)
    removed = [ln.strip() for ln in archives.lines if ln.strip().lower() in unmanaged]
    if removed:
        archives.lines = [ln for ln in archives.lines if ln.strip().lower() not in unmanaged]
    return removed
