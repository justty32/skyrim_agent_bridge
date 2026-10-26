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
  mo2ctl reconcile [--apply] [--source TAG] [--fail-on-drift]
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
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile
import bridge

from mo2ctl_archives import (  # noqa: F401
    candidate_mod_roots, choose_mod_root, extract_zip, find_case_insensitive,
    looks_like_mod_root, safe_rel, unpack_archive,
)
from mo2ctl_background import (  # noqa: F401
    background_active_backup, enable_background_active, restore_background_active,
)
from mo2ctl_crash import (  # noqa: F401
    crash_log_dir_from_status, crash_triage_from_capture, parse_crash_time, parse_uptime_ms,
    summarize_crash_log, uptime_bin,
)
from mo2ctl_env import (  # noqa: F401
    ARCHIVE_SUFFIXES, BACKGROUND_ACTIVE_BACKUP, BSA_SUFFIXES, DATA_DIR_NAMES,
    DEFAULT_HOUSECARL_SERVER, DEFAULT_MO2_ROOT, DEFAULT_PROFILE, ENGINE_LOADORDER_CHURN, Env,
    FOMOD_CHOICES_FORMAT, Fail, HOUSECARL_MCP_PROTOCOL, HOUSECARL_REQUEST_TIMEOUT_ENV,
    PLUGIN_SUFFIXES, PROFILE_MAIN_BRANCH, PROFILE_MANIFEST_FORMAT, PROFILE_STATE_FORMAT,
    STATIC_GATE_FORMAT, STEAM_APPID, load_env, read_selected_profile,
)
from mo2ctl_files import (  # noqa: F401
    BACKUP_DIR_NAME, BACKUP_KEEP, ProfileEdit, TextFile, atomic_write, backup_file, read_file,
    sha256_file, utc_stamp,
)
from mo2ctl_fomod_models import (  # noqa: F401
    FileInstall, FomodGroup, FomodPlan, FomodPlugin, ResolvedSource, default_group_picks,
)
from mo2ctl_fomod_parse import (  # noqa: F401
    all_children, child_text, file_installs, first_child, local_name, parse_fomod,
    parse_info_xml, plugin_type,
)
from mo2ctl_housecarl import (  # noqa: F401
    HousecarlClient, capture_static_gates, cmd_static_gates, load_json_file, static_tool_specs,
    write_json_file,
)
from mo2ctl_manifest import (  # noqa: F401
    _profile_file_state, archive_library_status, build_profile_state, profile_diff,
    read_manifest, remove_manifest_entry, update_manifest_for_install, write_manifest,
)
from mo2ctl_profile_git import (  # noqa: F401
    absorb_engine_churn, cmd_profile_semantics, dirty_paths, engine_churn_paths, git_branch,
    git_head, git_porcelain, git_profiles, manifest_from_git, profile_semantics,
    profile_text_at, require_clean_profile_repo, semantic_diff, slug_branch,
    text_file_from_string,
)
from mo2ctl_profile_lists import (  # noqa: F401
    add_archives, bsa_files, modlist_entries, modlist_index, plugin_files, plugin_insert_index,
    plugin_line_index, plugin_stems, priority_insert_index, priority_plugin_anchor,
    remove_archives, remove_plugins, set_mod_state,
)
from mo2ctl_sources import (  # noqa: F401
    cmd_inspect, copy_install, inspect_source, load_fomod_choices, materialize_fomod,
    resolve_source, selected_plugin_names, write_meta_ini,
)
from mo2ctl_static_gates import (  # noqa: F401
    classify_static_result, evaluate_static_gates, normalize_load_order_warnings, regex_count,
    skse_diagnostics, tool_key, validate_scripts_has_findings,
)


def write_profile_state(env: Env, state: dict) -> None:
    """Atomically write profiles/profile-state.json."""
    atomic_write(
        env.profile_state,
        (json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def commit_profile(env: Env, edit: ProfileEdit, *, source: str) -> dict:
    """Publish staged text, provenance, then the hash-bound live checkpoint.

    Caller must exclude MO2/other writers. Individual replacements are atomic;
    ordinary I/O failures roll back, while crash/interruption is detected by
    reconcile's checkpoint hashes (there is no multi-file atomic rename).
    """
    original = read_manifest(env)  # validate before touching profile bytes
    manifest = edit.manifest if edit.manifest is not None else original
    manifest_changed = manifest != original
    allowed = {env.modlist, env.plugins, env.loadorder, env.archives}
    if set(edit.files) - allowed:
        raise Fail("profile edit contains a file outside the selected profile")
    for path in (env.modlist, env.plugins, env.loadorder):
        edit.read(path)  # require the complete triplet before any writes
    entries = dict(modlist_entries(edit.read(env.modlist)))
    synced = []
    for name, entry in manifest["mods"].items():
        if name in entries and entry.get("enabled") != entries[name]:
            entry["enabled"] = entries[name]
            synced.append(name)

    manifest_written = manifest_changed or bool(synced)
    paths = [*edit.files, env.manifest, env.profile_state]
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    stats = {path: path.stat() for path in paths if path.exists()}
    changed = []
    try:
        for path, tf in edit.files.items():
            data = (tf.eol.join(tf.lines) + (tf.eol if tf.trailing_eol else "")).encode("utf-8")
            if data != before[path]:
                backup_file(path)
                changed.append(path)
                atomic_write(path, data)
        if manifest_written:
            changed.append(env.manifest)
            write_manifest(env, manifest, source=source)
        state = build_profile_state(env, source=source, manifest_dirty=edit.manifest_skipped)
        changed.append(env.profile_state)
        write_profile_state(env, state)  # publish last; binds both ledgers to bytes
    except Exception:
        for path in reversed(changed):
            if before[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, before[path])
                stat = stats[path]
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        raise
    state_summary = {
        "format": state["format"],
        "profile": state["profile"],
        "checkpoint_at": state["checkpoint_at"],
        "source": state["source"],
        "manifest_dirty": state["manifest_dirty"],
        "manifest_sha256": state["manifest_sha256"],
        "files": state["files"],
        "enabled_count": state["enabled_count"],
        "unregistered_count": len(state["unregistered"]),
        "stale_count": len(state["stale"]),
        "enabled_mismatch_count": len(state["enabled_mismatch"]),
    }
    return {
        "manifest_written": manifest_written,
        "manifest_synced": sorted(synced),
        "state": state_summary,
    }


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


def place_mod(tf: TextFile, name: str, enabled: bool, priority: str) -> str:
    prefix = "+" if enabled else "-"
    old_idx = modlist_index(tf, name)
    if old_idx is not None:
        tf.lines.pop(old_idx)
    insert_at = priority_insert_index(tf, priority)
    tf.lines.insert(insert_at, prefix + name)
    return priority


def add_plugins(
    env: Env,
    names: list[str],
    *,
    after: str | None = None,
    before: str | None = None,
    edit: ProfileEdit | None = None,
) -> list[str]:
    if after and before:
        raise Fail("plugin placement cannot specify both after and before")
    if not names:
        return []

    own_edit = edit is None
    edit = edit if edit is not None else ProfileEdit()
    plugins = edit.read(env.plugins)
    order = edit.read(env.loadorder)
    plugins_at = plugin_insert_index(plugins, after=after, before=before)
    order_at = plugin_insert_index(order, after=after, before=before)

    requested = {name.lower() for name in names}
    enabled = {
        ln.lstrip("*").strip().lower()
        for ln in plugins.lines
        if ln.startswith("*") and ln.lstrip("*").strip()
    }
    have = {ln.lstrip("*").strip().lower() for ln in plugins.lines if ln and not ln.startswith("#")}
    added = [name for name in names if name.lower() not in have]
    activated = [name for name in names if name.lower() not in enabled]
    for index, line in enumerate(plugins.lines):
        if (line and not line.startswith(("#", "*"))
                and line.lstrip("*").strip().lower() in requested):
            plugins.lines[index] = "*" + line
    if activated:
        plugins.lines[plugins_at:plugins_at] = ["*" + name for name in added]

    have = {ln.strip().lower() for ln in order.lines if ln and not ln.startswith("#")}
    order_added = [name for name in names if name.lower() not in have]
    if order_added:
        order.lines[order_at:order_at] = order_added

    if own_edit:
        commit_profile(env, edit, source="mo2ctl:add-plugins")
    return activated


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


def cmd_install(env: Env, args) -> dict:
    require_writable(args)
    edit = ProfileEdit()

    resolved = resolve_source(
        Path(args.source),
        args.name,
        Path(args.fomod_choices) if getattr(args, "fomod_choices", None) else None,
    )
    src_dir, loose, name = resolved.source_dir, resolved.loose_files, resolved.name
    dest = env.mods / name
    owns_dest = False

    try:
        if dest.exists():
            if not args.force:
                raise Fail(f"mod folder already exists: {dest} (use --force to replace)")
            shutil.rmtree(dest)
        owns_dest = True

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

        tf = edit.read(env.modlist)
        priority = place_mod(tf, name, not args.no_enable, getattr(args, "priority", "bottom"))

        plugins = plugin_files(dest)
        plugin_after = getattr(args, "plugin_after", None)
        plugin_before = None
        if plugin_after is None:
            plugin_after, plugin_before = priority_plugin_anchor(env, priority)
        activated = (
            add_plugins(env, plugins, after=plugin_after, before=plugin_before, edit=edit)
            if not args.no_enable else []
        )
        archives = bsa_files(dest)
        archives_added = add_archives(env, archives, plugins, edit) if not args.no_enable else []

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
        if getattr(args, "no_manifest", False):
            edit.manifest_skipped = "--no-manifest"
        else:
            manifest = read_manifest(env)
            result["manifest"] = update_manifest_for_install(
                env, manifest, result, resolved, args,
            )
            edit.manifest = manifest
        result["profile_commit"] = commit_profile(env, edit, source="mo2ctl:install")
        return result
    except Exception:
        if owns_dest:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        if resolved.cleanup:
            shutil.rmtree(resolved.cleanup, ignore_errors=True)


def cmd_uninstall(env: Env, args) -> dict:
    require_writable(args)
    edit = ProfileEdit()

    name = args.name
    dest = env.mods / name
    plugins = plugin_files(dest) if dest.is_dir() else []
    archives = bsa_files(dest) if dest.is_dir() else []

    tf = edit.read(env.modlist)
    idx = modlist_index(tf, name)
    if idx is not None:
        tf.lines.pop(idx)

    removed_plugins = remove_plugins(env, plugins, edit)
    removed_archives = remove_archives(env, archives, plugins, edit)

    removed_files = dest.is_dir() and not args.keep_files

    if idx is None and not removed_files and not removed_plugins and not removed_archives:
        raise Fail(f"nothing to uninstall: {name} is not in the modlist and has no folder")

    if getattr(args, "keep_manifest", False):
        manifest_removed = False
        edit.manifest_skipped = "--keep-manifest"
    else:
        manifest = read_manifest(env)
        manifest_removed = remove_manifest_entry(manifest, name)
        edit.manifest = manifest

    result = {
        "uninstalled": name,
        "removed_from_modlist": idx is not None,
        "removed_plugins": removed_plugins,
        "removed_archives": removed_archives,
        "removed_files": removed_files,
        "removed_manifest": manifest_removed,
    }
    result["profile_commit"] = commit_profile(env, edit, source="mo2ctl:uninstall")
    if removed_files:
        shutil.rmtree(dest)
    return result


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


def cmd_profile_absorb_churn(env: Env, args) -> dict:
    require_writable(args)
    return absorb_engine_churn(env, args.message, force=args.force)


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

    result = {
        "failed": branch,
        "checked_out": PROFILE_MAIN_BRANCH,
        "deleted_branch": branch if branch != PROFILE_MAIN_BRANCH else None,
        "uninstalled": uninstall_results,
        "head": git_head(env),
    }
    result["profile_commit"] = commit_profile(
        env, ProfileEdit(), source="mo2ctl:try-fail",
    )
    # Retain the new checkpoint even in legacy repos which do not ignore it.
    # Record only generated ledger changes; never clean away the checkpoint.
    checkpoint_paths = []
    for path in (env.manifest, env.profile_state):
        if path.is_file() and git_profiles(
                env, ["check-ignore", "-q", "--", path.name], check=False).returncode != 0:
            checkpoint_paths.append(path.name)
    if checkpoint_paths:
        git_profiles(env, ["add", "--", *checkpoint_paths])
        if git_profiles(env, ["diff", "--cached", "--quiet"], check=False).returncode:
            git_profiles(env, ["commit", "-m", "Checkpoint restored profile after try-fail"])
    result["head"] = git_head(env)
    return result


def cmd_try_pass(env: Env, args) -> dict:
    require_writable(args)
    branch = git_branch(env)
    if not branch.startswith("try/") and not args.force:
        raise Fail(f"refusing to pass non-try branch: {branch} (use --force to override)")

    checkpoint = commit_profile(env, ProfileEdit(), source="mo2ctl:try-pass")
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
        "profile_commit": checkpoint,
        "checked_out": PROFILE_MAIN_BRANCH,
        "committed": committed,
        "merged_head": git_head(env),
        "deleted_branch": branch if args.delete_branch and branch != PROFILE_MAIN_BRANCH else None,
    }


def cmd_enable(env: Env, args) -> dict:
    require_writable(args)
    edit = ProfileEdit()
    result = set_mod_state(env, args.name, True, edit)
    plugins = plugin_files(env.mods / args.name) if (env.mods / args.name).is_dir() else []
    output = {"mod": args.name, "enabled": True, "modlist": result,
              "plugins_activated": add_plugins(
                  env, plugins, after=getattr(args, "plugin_after", None), edit=edit,
              )}
    output["profile_commit"] = commit_profile(
        env, edit, source="mo2ctl:enable",
    )
    return output


def cmd_disable(env: Env, args) -> dict:
    require_writable(args)
    edit = ProfileEdit()
    result = set_mod_state(env, args.name, False, edit)
    plugins = plugin_files(env.mods / args.name) if (env.mods / args.name).is_dir() else []
    output = {"mod": args.name, "enabled": False, "modlist": result,
              "plugins_deactivated": remove_plugins(env, plugins, edit)}
    output["profile_commit"] = commit_profile(
        env, edit, source="mo2ctl:disable",
    )
    return output


def cmd_reconcile(env: Env, args) -> dict:
    """Report profile drift and optionally refresh provenance/checkpoint state."""
    diff = profile_diff(env)
    checkpoint_state = None
    if env.profile_state.is_file():
        try:
            checkpoint_state = json.loads(env.profile_state.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Fail(f"cannot parse profile state: {env.profile_state}: {exc}") from exc

    checkpoint = None
    changed_files = []
    checkpoint_missing = checkpoint_state is None
    profile_mismatch = bool(checkpoint_state and checkpoint_state.get("profile") != env.profile)
    manifest_changed = bool(checkpoint_state and
                            checkpoint_state.get("manifest_sha256") != sha256_file(env.manifest))
    if checkpoint_state is not None:
        checkpoint = {
            "checkpoint_at": checkpoint_state.get("checkpoint_at"),
            "source": checkpoint_state.get("source"),
        }
        previous_files = checkpoint_state.get("files", {})
        for name, path in (
            ("modlist", env.modlist),
            ("plugins", env.plugins),
            ("loadorder", env.loadorder),
        ):
            previous = previous_files.get(name, {})
            if sha256_file(path) != previous.get("sha256"):
                changed_files.append(name)

    result = {
        "modlist_enabled": diff["modlist_enabled"],
        "manifest_mods": diff["manifest_mods"],
        "only_in_modlist": len(diff["only_in_modlist"]),
        "only_in_manifest": len(diff["only_in_manifest"]),
        "enabled_mismatch": len(diff["enabled_mismatch"]),
        "checkpoint": checkpoint,
        "checkpoint_missing": checkpoint_missing,
        "profile_mismatch": profile_mismatch,
        "manifest_changed": manifest_changed,
        "external_write_detected": bool(checkpoint and changed_files),
        "changed_files": changed_files,
        "applied": False,
    }

    if getattr(args, "apply", False):
        require_writable(args)
        result["applied"] = True
        result.update(commit_profile(
            env,
            ProfileEdit(manifest=None),
            source=f"reconcile:{getattr(args, 'source', 'external')}",
        ))
        applied_diff = profile_diff(env)
        result.update({
            "modlist_enabled": applied_diff["modlist_enabled"],
            "manifest_mods": applied_diff["manifest_mods"],
            "only_in_modlist": len(applied_diff["only_in_modlist"]),
            "only_in_manifest": len(applied_diff["only_in_manifest"]),
            "enabled_mismatch": len(applied_diff["enabled_mismatch"]),
        })
    elif getattr(args, "fail_on_drift", False) and (
            result["external_write_detected"] or checkpoint_missing or profile_mismatch
            or manifest_changed or diff["enabled_mismatch"]):
        raise Fail("profile drift detected")

    result["diff_summary"] = (
        f"enabled={result['modlist_enabled']} provenance={result['manifest_mods']} "
        f"unregistered={result['only_in_modlist']} stale={result['only_in_manifest']} "
        f"enabled_mismatch={result['enabled_mismatch']}"
    )
    return result


def launch_script_path() -> Path:
    override = os.environ.get("MO2CTL_LAUNCH_SCRIPT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "instance" / "tools" / "launch-mo2.sh"


def cmd_launch(env: Env, args) -> dict:
    if game_pids():
        raise Fail("Skyrim is already running (mo2ctl kill first)")

    selected = read_selected_profile(env.root)
    if selected != env.profile:
        raise Fail(
            f"MO2 selected profile is {selected!r}, but launch targets {env.profile!r}; "
            f"run `mo2ctl select-profile {env.profile}` before launch"
        )

    script = launch_script_path()
    if not script.is_file():
        raise Fail(
            f"MO2 launch script is not a file: {script}; set MO2CTL_LAUNCH_SCRIPT "
            "or launch manually with `instance/tools/launch-mo2.sh --skse`"
        )

    background_active = None
    if getattr(args, "background_active", False):
        if mo2_pids():
            raise Fail("MO2 is already running (mo2ctl kill --mo2 first)")
        background_active = enable_background_active(env)

    cmd = [str(script), "--shortcut", args.shortcut]
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
    s.add_argument("--plugin-after", help="place new plugins immediately after this plugin")
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
    s.add_argument(
        "--crash-since",
        help="ISO timestamp; explicit offsets recommended, while offset-free values use local time",
    )
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
    s.add_argument("--plugin-after", help="place new plugins immediately after this plugin")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_enable)

    s = sub_add("disable", "turn a mod off in the profile")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_disable)

    s = sub_add("reconcile", "report external profile drift or refresh the checkpoint")
    s.add_argument("--apply", action="store_true",
                   help="sync existing manifest enabled flags and write profile-state.json")
    s.add_argument("--source", default="external",
                   help="source tag used with --apply (default: external)")
    s.add_argument("--fail-on-drift", action="store_true",
                   help="fail dry-run when external writes or enabled mismatches are found")
    s.add_argument("--force", action="store_true",
                   help="ignore the running-process lock with --apply")
    s.set_defaults(func=cmd_reconcile)

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
