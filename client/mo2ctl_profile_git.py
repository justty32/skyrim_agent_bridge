"""Profile Git 與語意差異。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    ENGINE_LOADORDER_CHURN, Env, Fail, PROFILE_MANIFEST_FORMAT,
)
from mo2ctl_files import (  # noqa: F401
    TextFile,
)
from mo2ctl_profile_lists import (  # noqa: F401
    modlist_entries,
)


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
