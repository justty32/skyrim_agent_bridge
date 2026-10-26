"""FOMOD XML 解析。"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from mo2ctl_archives import (  # noqa: F401
    find_case_insensitive, safe_rel,
)
from mo2ctl_env import (  # noqa: F401
    Fail,
)
from mo2ctl_fomod_models import (  # noqa: F401
    FileInstall, FomodGroup, FomodPlan, FomodPlugin,
)


def local_name(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if local_name(child) == name and child.text:
            return child.text.strip()
    return None


def first_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if local_name(child) == name:
            return child
    return None


def all_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if local_name(child) == name]


def file_installs(parent: ET.Element) -> list[FileInstall]:
    installs: list[FileInstall] = []
    for elem in parent.iter():
        tag = local_name(elem)
        if tag not in {"file", "folder"}:
            continue
        source = elem.get("source")
        if not source:
            continue
        target = elem.get("destination") or elem.get("target") or ""
        installs.append(FileInstall(str(safe_rel(source)), str(safe_rel(target)) if target else ""))
    return installs


def plugin_type(plugin: ET.Element, unsupported: list[str], label: str) -> str:
    descriptor = first_child(plugin, "typeDescriptor")
    if descriptor is None:
        return "Optional"
    dependency = first_child(descriptor, "dependencyType")
    if dependency is not None:
        unsupported.append(f"{label}: dependencyType requires runtime condition evaluation")
        return "Conditional"
    type_elem = first_child(descriptor, "type")
    return type_elem.get("name", "Optional") if type_elem is not None else "Optional"


def parse_info_xml(root: Path) -> tuple[str | None, str | None]:
    info = find_case_insensitive(root, "fomod/info.xml")
    if not info:
        return None, None
    try:
        doc = ET.parse(info).getroot()
    except ET.ParseError as exc:
        raise Fail(f"handoff_user: cannot parse fomod/info.xml: {exc}") from exc
    return child_text(doc, "Name"), child_text(doc, "Version")


def parse_fomod(root: Path) -> FomodPlan | None:
    config = find_case_insensitive(root, "fomod/ModuleConfig.xml")
    if not config:
        return None
    try:
        doc = ET.parse(config).getroot()
    except ET.ParseError as exc:
        raise Fail(f"handoff_user: cannot parse fomod/ModuleConfig.xml: {exc}") from exc

    name, version = parse_info_xml(root)
    name = name or doc.get("moduleName") or child_text(doc, "moduleName")
    unsupported: list[str] = []

    required_files: list[FileInstall] = []
    install_steps = first_child(doc, "installSteps")
    required = first_child(doc, "requiredInstallFiles")
    if required is not None:
        required_files.extend(file_installs(required))

    conditional = first_child(doc, "conditionalFileInstalls")
    if conditional is not None and list(conditional):
        unsupported.append("conditionalFileInstalls requires runtime flag evaluation")

    groups: list[FomodGroup] = []
    if install_steps is not None:
        for step in all_children(install_steps, "installStep"):
            step_name = step.get("name") or "Install"
            visible = first_child(step, "visible")
            if visible is not None and list(visible):
                unsupported.append(f"{step_name}: visible conditions are not supported")
            optional_file_groups = first_child(step, "optionalFileGroups")
            if optional_file_groups is None:
                continue
            for group in all_children(optional_file_groups, "group"):
                group_name = group.get("name") or "Options"
                select_type = group.get("type") or "SelectAny"
                plugins_elem = first_child(group, "plugins")
                plugins: list[FomodPlugin] = []
                if plugins_elem is not None:
                    for plugin in all_children(plugins_elem, "plugin"):
                        plugin_name = plugin.get("name") or "Option"
                        label = f"{step_name}/{group_name}/{plugin_name}"
                        files_elem = first_child(plugin, "files")
                        plugins.append(FomodPlugin(
                            step=step_name,
                            group=group_name,
                            name=plugin_name,
                            type_name=plugin_type(plugin, unsupported, label),
                            description=child_text(plugin, "description") or "",
                            files=file_installs(files_elem) if files_elem is not None else [],
                        ))
                groups.append(FomodGroup(step=step_name, name=group_name,
                                         select_type=select_type, plugins=plugins))

    return FomodPlan(name=name, version=version, required_files=required_files,
                     groups=groups, unsupported=unsupported)
