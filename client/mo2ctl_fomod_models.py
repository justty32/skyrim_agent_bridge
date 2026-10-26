"""FOMOD 資料型別與預設選項。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    FOMOD_CHOICES_FORMAT,
)


# ---------------------------------------------------------------------------
# Archive + FOMOD resolution
# ---------------------------------------------------------------------------


@dataclass
class FileInstall:
    source: str
    target: str


@dataclass
class FomodPlugin:
    step: str
    group: str
    name: str
    type_name: str
    description: str
    files: list[FileInstall]

    @property
    def choice_id(self) -> str:
        return f"{self.step}/{self.group}/{self.name}"


@dataclass
class FomodGroup:
    step: str
    name: str
    select_type: str
    plugins: list[FomodPlugin]


@dataclass
class FomodPlan:
    name: str | None
    version: str | None
    required_files: list[FileInstall]
    groups: list[FomodGroup]
    unsupported: list[str]

    def default_choices(self) -> dict:
        selected: dict[str, dict[str, list[str]]] = {}
        handoff: list[str] = list(self.unsupported)
        for group in self.groups:
            picks = default_group_picks(group)
            if picks is None:
                handoff.append(
                    f"{group.step}/{group.name}: no deterministic default for {group.select_type}"
                )
                picks = []
            selected.setdefault(group.step, {})[group.name] = picks
        status = "handoff_user" if handoff else "ready"
        return {
            "format": FOMOD_CHOICES_FORMAT,
            "status": status,
            "mod_name": self.name,
            "version": self.version,
            "selected": selected,
            "handoff_reasons": handoff,
        }

    def summary(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "required_files": [fi.__dict__ for fi in self.required_files],
            "groups": [
                {
                    "step": group.step,
                    "name": group.name,
                    "select_type": group.select_type,
                    "plugins": [
                        {
                            "name": plugin.name,
                            "type": plugin.type_name,
                            "description": plugin.description,
                            "files": [fi.__dict__ for fi in plugin.files],
                            "choice_id": plugin.choice_id,
                        }
                        for plugin in group.plugins
                    ],
                }
                for group in self.groups
            ],
            "unsupported": self.unsupported,
            "default_choices": self.default_choices(),
        }


@dataclass
class ResolvedSource:
    source_dir: Path | None
    loose_files: list[Path]
    name: str
    version: str | None = None
    warnings: list[str] | None = None
    fomod: FomodPlan | None = None
    fomod_choices: dict | None = None
    cleanup: Path | None = None


def default_group_picks(group: FomodGroup) -> list[str] | None:
    if group.select_type == "SelectAll":
        return [p.name for p in group.plugins]
    required = [p.name for p in group.plugins if p.type_name.lower() == "required"]
    recommended = [p.name for p in group.plugins if p.type_name.lower() == "recommended"]
    selected = required + recommended
    if group.select_type == "SelectAny":
        return selected
    if group.select_type == "SelectAtLeastOne":
        return selected if selected else None
    if group.select_type == "SelectExactlyOne":
        if len(required) == 1:
            return required
        if not required and len(recommended) == 1:
            return recommended
        return None
    if group.select_type == "SelectAtMostOne":
        if len(selected) <= 1:
            return selected
        return None
    return None
