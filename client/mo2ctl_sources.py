"""FOMOD materialization 與安裝來源解析。"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from mo2ctl_archives import (  # noqa: F401
    choose_mod_root, looks_like_mod_root, safe_rel, unpack_archive,
)
from mo2ctl_env import (  # noqa: F401
    ARCHIVE_SUFFIXES, Env, FOMOD_CHOICES_FORMAT, Fail, PLUGIN_SUFFIXES,
)
from mo2ctl_fomod_models import (  # noqa: F401
    FileInstall, FomodPlan, ResolvedSource,
)
from mo2ctl_fomod_parse import (  # noqa: F401
    parse_fomod,
)


def load_fomod_choices(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FOMOD_CHOICES_FORMAT:
        raise Fail(f"{path} is not a {FOMOD_CHOICES_FORMAT} file")
    if data.get("status") == "handoff_user":
        reasons = "; ".join(data.get("handoff_reasons") or ["manual choices required"])
        raise Fail(f"handoff_user: choices file is not replayable: {reasons}")
    return data


def selected_plugin_names(choices: dict, step: str, group: str) -> set[str]:
    selected = choices.get("selected", {})
    return set(selected.get(step, {}).get(group, []))


def copy_install(root: Path, install: FileInstall, dest: Path) -> None:
    src = root / safe_rel(install.source)
    if not src.exists():
        raise Fail(f"handoff_user: FOMOD references missing source: {install.source}")
    if src.is_dir():
        out = dest / safe_rel(install.target) if install.target else dest
        if out.exists() and not out.is_dir():
            raise Fail(f"cannot merge folder over file: {install.target}")
        shutil.copytree(src, out, dirs_exist_ok=True)
    else:
        target_rel = safe_rel(install.target) if install.target else Path(src.name)
        out = dest / target_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


def materialize_fomod(root: Path, plan: FomodPlan, choices: dict, dest: Path) -> None:
    if plan.unsupported:
        raise Fail(f"handoff_user: unsupported FOMOD features: {'; '.join(plan.unsupported)}")
    dest.mkdir(parents=True)
    for install in plan.required_files:
        copy_install(root, install, dest)
    known = {(group.step, group.name, plugin.name)
             for group in plan.groups for plugin in group.plugins}
    requested = []
    for step, groups in choices.get("selected", {}).items():
        for group, plugins in groups.items():
            for plugin in plugins:
                requested.append((step, group, plugin))
    unknown = ["/".join(item) for item in requested if item not in known]
    if unknown:
        raise Fail(f"choices reference unknown FOMOD plugin(s): {', '.join(unknown)}")
    for group in plan.groups:
        selected = selected_plugin_names(choices, group.step, group.name)
        for plugin in group.plugins:
            if plugin.name in selected:
                for install in plugin.files:
                    copy_install(root, install, dest)


def inspect_source(src: Path) -> dict:
    src = src.expanduser().resolve()
    if not src.exists():
        raise Fail(f"source not found: {src}")
    with tempfile.TemporaryDirectory(prefix="mo2ctl-inspect-") as tmp:
        root = Path(tmp)
        source_root = unpack_archive(src, root) if src.is_file() and src.suffix.lower() in ARCHIVE_SUFFIXES else src
        mod_root = choose_mod_root(source_root) if source_root.is_dir() else source_root
        fomod = parse_fomod(mod_root) if mod_root.is_dir() else None
        return {
            "source": str(src),
            "source_type": src.suffix.lower().lstrip(".") if src.is_file() else "directory",
            "mod_root": str(mod_root),
            "looks_like_mod_root": mod_root.is_dir() and looks_like_mod_root(mod_root),
            "has_fomod": fomod is not None,
            "fomod": fomod.summary() if fomod else None,
        }


def resolve_source(src: Path, name: str | None, choices_path: Path | None = None) -> ResolvedSource:
    """Work out what to copy and what to call it.

    A bare .esp is accepted as a source, because that is exactly what ModForge
    writes into `out/`. Archives are unpacked into a temporary staging directory
    and then resolved to either a Data-level root or a materialized FOMOD result.
    """
    src = src.expanduser().resolve()
    if not src.exists():
        raise Fail(f"source not found: {src}")

    if src.is_file() and src.suffix.lower() in PLUGIN_SUFFIXES:
        return ResolvedSource(None, [src], name or src.stem)

    warnings: list[str] = []
    if src.is_file():
        work = Path(tempfile.mkdtemp(prefix="mo2ctl-install-"))
        source_root = unpack_archive(src, work)
        base_name = src.stem
    else:
        work = None
        source_root = src
        base_name = src.name

    mod_root = choose_mod_root(source_root)
    fomod = parse_fomod(mod_root)
    if fomod:
        if choices_path is None:
            choices = fomod.default_choices()
            if choices.get("status") == "handoff_user":
                reasons = "; ".join(choices.get("handoff_reasons") or [])
                raise Fail(f"handoff_user: FOMOD choices required for {src.name}: {reasons}")
        else:
            choices = load_fomod_choices(choices_path.expanduser())
        material_work = work or Path(tempfile.mkdtemp(prefix="mo2ctl-fomod-"))
        staging = material_work / "materialized"
        materialize_fomod(mod_root, fomod, choices, staging)
        if not any(staging.iterdir()):
            raise Fail("handoff_user: FOMOD choices materialized an empty mod")
        return ResolvedSource(staging, [], name or fomod.name or base_name,
                              fomod.version, warnings, fomod, choices, material_work)

    children = [p for p in mod_root.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and children[0].name.lower() == "data":
        return ResolvedSource(children[0], [], name or base_name, warnings=warnings, cleanup=work)

    return ResolvedSource(mod_root, [], name or base_name, warnings=warnings, cleanup=work)


def cmd_inspect(env: Env, args) -> dict:
    result = inspect_source(Path(args.source))
    if args.write_choices:
        fomod = result.get("fomod")
        if not fomod:
            raise Fail("source has no FOMOD choices to write")
        out = Path(args.write_choices).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fomod["default_choices"], indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        result["choices_written"] = str(out)
    return result


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
