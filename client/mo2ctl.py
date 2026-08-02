#!/usr/bin/env python3
"""mo2ctl — drive Mod Organizer 2 from the Linux side without opening its GUI.

The Linux half of the AI QA loop (plan: workflows/plans/ai-ingame-qa-loop.md, Phase 2.1).
Installs a mod folder into MO2, flips it on and off in the profile, starts SKSE through
MO2, kills the game, and reports whether any of that is currently safe to do.

stdlib only, on purpose: this runs before anything is built and has to keep working when
the rest of the toolchain is mid-rebuild.

  mo2ctl status [--json]
  mo2ctl install <dir-or-esp> [--name NAME] [--no-enable] [--force]
  mo2ctl uninstall <name> [--keep-files]
  mo2ctl enable <name>
  mo2ctl disable <name>
  mo2ctl launch [--wait SECONDS] [--no-wait]
  mo2ctl kill [--mo2]

Everything that mutates MO2 state refuses to run while MO2 or the game is up; see
`profile_lock_reason` for why that is not merely cautious.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import bridge

DEFAULT_MO2_ROOT = Path.home() / "games/mod-organizer-2-skyrimspecialedition/modorganizer2"
DEFAULT_PROFILE = "Default"
STEAM_APPID = "489830"

PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")

# Directory names that make a folder recognisable as a Skyrim `Data`-relative mod root.
DATA_DIR_NAMES = {
    "skse", "meshes", "textures", "scripts", "sound", "interface", "seq",
    "music", "video", "grass", "lodsettings", "shadersfx", "strings", "dyndolod",
    "netscriptframework", "source", "calientetools", "tools", "docs",
}


class Fail(Exception):
    """A problem worth reporting to the caller, not a traceback."""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


@dataclass
class Env:
    root: Path
    profile: str

    @property
    def mods(self) -> Path:
        return self.root / "mods"

    @property
    def profile_dir(self) -> Path:
        return self.root / "profiles" / self.profile

    @property
    def modlist(self) -> Path:
        return self.profile_dir / "modlist.txt"

    @property
    def plugins(self) -> Path:
        return self.profile_dir / "plugins.txt"

    @property
    def loadorder(self) -> Path:
        return self.profile_dir / "loadorder.txt"

    @property
    def mo2_exe(self) -> Path:
        return self.root / "ModOrganizer.exe"


def load_env() -> Env:
    root = Path(os.environ.get("MO2_ROOT", DEFAULT_MO2_ROOT)).expanduser()
    if not root.is_dir():
        raise Fail(f"MO2 root not found: {root} (set MO2_ROOT)")

    profile = os.environ.get("MO2_PROFILE") or read_selected_profile(root) or DEFAULT_PROFILE
    env = Env(root=root, profile=profile)
    if not env.profile_dir.is_dir():
        raise Fail(f"profile not found: {env.profile_dir} (set MO2_PROFILE)")
    return env


def read_selected_profile(root: Path) -> str | None:
    """Pull `selected_profile` out of ModOrganizer.ini.

    MO2 stores it Qt-style as `selected_profile=@ByteArray(Default)`.
    """
    ini = root / "ModOrganizer.ini"
    if not ini.is_file():
        return None
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "selected_profile":
            continue
        value = value.strip()
        if value.startswith("@ByteArray(") and value.endswith(")"):
            value = value[len("@ByteArray("):-1]
        return value or None
    return None


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


def write_file(tf: TextFile, *, backup: bool = True) -> Path | None:
    made = backup_file(tf.path) if backup else None
    text = tf.eol.join(tf.lines) + (tf.eol if tf.trailing_eol else "")
    tf.path.write_bytes(text.encode("utf-8"))
    return made


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


# ---------------------------------------------------------------------------
# modlist.txt
#
# Line 1 is MO2's header comment and must survive. Entries are `+Name` (enabled)
# or `-Name` (disabled), and the file reads top = highest priority, so a new mod
# goes at line 2 to win conflicts — which is what you want for a thing under test.
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


def set_mod_state(env: Env, name: str, enabled: bool) -> str:
    tf = read_file(env.modlist)
    idx = modlist_index(tf, name)
    if idx is None:
        raise Fail(f"mod not in {env.profile} modlist: {name}")
    prefix = "+" if enabled else "-"
    if tf.lines[idx].startswith(prefix):
        return "unchanged"
    tf.lines[idx] = prefix + tf.lines[idx][1:]
    write_file(tf)
    return "changed"


# ---------------------------------------------------------------------------
# plugins.txt / loadorder.txt
#
# plugins.txt marks active plugins with a leading `*`; loadorder.txt lists every
# known plugin bare, in order. Appending puts the new plugin last, which is where
# a mod under test wants to be: later wins.
# ---------------------------------------------------------------------------


def plugin_files(mod_dir: Path) -> list[str]:
    return sorted(
        p.name for p in mod_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PLUGIN_SUFFIXES
    )


def add_plugins(env: Env, names: list[str]) -> list[str]:
    if not names:
        return []
    added = []

    plugins = read_file(env.plugins)
    have = {ln.lstrip("*").strip().lower() for ln in plugins.lines if ln and not ln.startswith("#")}
    for name in names:
        if name.lower() in have:
            continue
        plugins.lines.append("*" + name)
        added.append(name)
    if added:
        write_file(plugins)

    order = read_file(env.loadorder)
    have = {ln.strip().lower() for ln in order.lines if ln and not ln.startswith("#")}
    changed = False
    for name in names:
        if name.lower() not in have:
            order.lines.append(name)
            changed = True
    if changed:
        write_file(order)

    return added


def remove_plugins(env: Env, names: list[str]) -> list[str]:
    if not names:
        return []
    drop = {n.lower() for n in names}
    removed = []
    for path in (env.plugins, env.loadorder):
        tf = read_file(path)
        keep = [ln for ln in tf.lines if ln.lstrip("*").strip().lower() not in drop]
        if len(keep) != len(tf.lines):
            removed.extend(n for n in names if n not in removed)
            tf.lines = keep
            write_file(tf)
    return removed


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


def resolve_source(src: Path, name: str | None) -> tuple[Path | None, list[Path], str]:
    """Work out what to copy and what to call it.

    Returns (source_dir_or_None, loose_files, mod_name). A bare .esp is accepted
    as a source, because that is exactly what ModForge writes into `out/`.
    """
    src = src.expanduser().resolve()
    if not src.exists():
        raise Fail(f"source not found: {src}")

    if src.is_file():
        if src.suffix.lower() not in PLUGIN_SUFFIXES:
            raise Fail(f"source file is not a plugin: {src.name}")
        return None, [src], name or src.stem

    # A folder holding nothing but `Data/` is the shape a lot of archives unpack
    # into; MO2 wants the contents of Data, not Data itself.
    children = [p for p in src.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and children[0].name.lower() == "data":
        return children[0], [], name or src.name

    return src, [], name or src.name


def looks_like_mod_root(path: Path) -> bool:
    for child in path.iterdir():
        if child.is_dir() and child.name.lower() in DATA_DIR_NAMES:
            return True
        if child.is_file() and child.suffix.lower() in (*PLUGIN_SUFFIXES, ".bsa", ".ini"):
            return True
    return False


def cmd_install(env: Env, args) -> dict:
    require_writable(args)

    src_dir, loose, name = resolve_source(Path(args.source), args.name)
    dest = env.mods / name

    if dest.exists():
        if not args.force:
            raise Fail(f"mod folder already exists: {dest} (use --force to replace)")
        shutil.rmtree(dest)

    warnings = []
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

    write_meta_ini(dest, args.version, args.comment)

    tf = read_file(env.modlist)
    idx = modlist_index(tf, name)
    prefix = "+" if not args.no_enable else "-"
    if idx is None:
        # Line 0 is MO2's header comment; line 1 is top priority.
        tf.lines.insert(1, prefix + name)
    else:
        tf.lines[idx] = prefix + name
    write_file(tf)

    plugins = plugin_files(dest)
    activated = add_plugins(env, plugins) if not args.no_enable else []

    return {
        "installed": name,
        "path": str(dest),
        "enabled": not args.no_enable,
        "plugins_found": plugins,
        "plugins_activated": activated,
        "warnings": warnings,
    }


def write_meta_ini(dest: Path, version: str, comment: str) -> None:
    (dest / "meta.ini").write_text(
        "[General]\n"
        "gameName=Skyrim Special Edition\n"
        "modid=0\n"
        f"version={version}\n"
        "category=0\n"
        f"comments={comment}\n",
        encoding="utf-8",
    )


def cmd_uninstall(env: Env, args) -> dict:
    require_writable(args)

    name = args.name
    dest = env.mods / name
    plugins = plugin_files(dest) if dest.is_dir() else []

    tf = read_file(env.modlist)
    idx = modlist_index(tf, name)
    if idx is not None:
        tf.lines.pop(idx)
        write_file(tf)

    removed_plugins = remove_plugins(env, plugins)

    removed_files = False
    if dest.is_dir() and not args.keep_files:
        shutil.rmtree(dest)
        removed_files = True

    if idx is None and not removed_files and not removed_plugins:
        raise Fail(f"nothing to uninstall: {name} is not in the modlist and has no folder")

    return {
        "uninstalled": name,
        "removed_from_modlist": idx is not None,
        "removed_plugins": removed_plugins,
        "removed_files": removed_files,
    }


def cmd_enable(env: Env, args) -> dict:
    require_writable(args)
    result = set_mod_state(env, args.name, True)
    plugins = plugin_files(env.mods / args.name) if (env.mods / args.name).is_dir() else []
    return {"mod": args.name, "enabled": True, "modlist": result,
            "plugins_activated": add_plugins(env, plugins)}


def cmd_disable(env: Env, args) -> dict:
    require_writable(args)
    result = set_mod_state(env, args.name, False)
    plugins = plugin_files(env.mods / args.name) if (env.mods / args.name).is_dir() else []
    return {"mod": args.name, "enabled": False, "modlist": result,
            "plugins_deactivated": remove_plugins(env, plugins)}


def cmd_launch(env: Env, args) -> dict:
    if game_pids():
        raise Fail("Skyrim is already running (mo2ctl kill first)")

    # protontricks-launch runs the exe inside app 489830's Proton prefix, which is
    # where MO2 itself lives — usvfs needs MO2 and the game in one wine session.
    # `moshortcut://:SKSE` is MO2's own name for the customExecutables entry, so
    # this is the same path the GUI's Run button takes.
    cmd = [
        "protontricks-launch", "--appid", STEAM_APPID,
        str(env.mo2_exe), f"moshortcut://:{args.shortcut}",
    ]
    log_path = Path(os.environ.get("MO2CTL_LOG_DIR", "/tmp")) / "mo2ctl-launch.log"
    with open(log_path, "ab") as log:
        log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd)}\n".encode())
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True)

    result = {"launched": True, "pid": proc.pid, "shortcut": args.shortcut, "log": str(log_path)}
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
            return result
        time.sleep(2)

    result["bridge"] = {"reachable": False, "error": f"no /ping within {args.wait}s"}
    return result


def cmd_kill(env: Env, args) -> dict:
    targets = list(game_pids())
    if args.mo2:
        targets += mo2_pids()
    if not targets:
        return {"killed": [], "note": "nothing to kill"}

    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not [p for p in targets if Path(f"/proc/{p}").exists()]:
            return {"killed": targets, "escalated": []}
        time.sleep(0.5)

    escalated = []
    for pid in targets:
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
                escalated.append(pid)
            except ProcessLookupError:
                pass
    return {"killed": targets, "escalated": escalated}


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

    s = sub_add("install", "copy a mod folder (or a bare .esp) into MO2 and enable it")
    s.add_argument("source", help="mod folder, a folder containing Data/, or a single plugin file")
    s.add_argument("--name", help="mod folder name in MO2 (default: source basename)")
    s.add_argument("--no-enable", action="store_true", help="install but leave it off")
    s.add_argument("--force", action="store_true", help="replace an existing folder / ignore the running-process lock")
    s.add_argument("--version", default="0.0.0")
    s.add_argument("--comment", default="Installed by mo2ctl (AI QA loop). TEST HARNESS — safe to remove.")
    s.set_defaults(func=cmd_install)

    s = sub_add("uninstall", "remove a mod from the profile and delete its folder")
    s.add_argument("name")
    s.add_argument("--keep-files", action="store_true", help="deregister but leave mods/<name> on disk")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_uninstall)

    s = sub_add("enable", "turn a mod on in the profile")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_enable)

    s = sub_add("disable", "turn a mod off in the profile")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_disable)

    s = sub_add("launch", "start SKSE through MO2 inside the game's Proton prefix")
    s.add_argument("--shortcut", default="SKSE", help="MO2 customExecutables title (default: SKSE)")
    s.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the bridge (default: 180)")
    s.add_argument("--no-wait", action="store_true", help="return as soon as the launcher is spawned")
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
        result = args.func(load_env(), args)
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
