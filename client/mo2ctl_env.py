"""MO2 環境、共用常數與錯誤型別。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path



DEFAULT_MO2_ROOT = Path.home() / "games/mod-organizer-2-skyrimspecialedition/modorganizer2"
DEFAULT_PROFILE = "Default"
STEAM_APPID = "489830"

PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
BSA_SUFFIXES = (".bsa", ".ba2")
FOMOD_CHOICES_FORMAT = "mo2ctl-fomod-choices-v1"
PROFILE_MANIFEST_FORMAT = "mo2ctl-profile-manifest-v1"
PROFILE_STATE_FORMAT = "mo2ctl-profile-state-v1"
STATIC_GATE_FORMAT = "mo2ctl-static-gates-v1"
PROFILE_MAIN_BRANCH = "main"
DEFAULT_HOUSECARL_SERVER = Path.home() / "tools/housecarl/server/housecarl-mcp"
HOUSECARL_MCP_PROTOCOL = "2025-06-18"
HOUSECARL_REQUEST_TIMEOUT_ENV = "MO2CTL_HOUSECARL_TIMEOUT"
ENGINE_LOADORDER_CHURN = {
    "ccbgssse068-bloodfall.esl",
    "ccbgssse069-contest.esl",
    "ccvsvsse004-beafarmer.esl",
}
BACKGROUND_ACTIVE_BACKUP = "background-active-skyrim.ini"

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
    def profiles_repo(self) -> Path:
        return self.root / "profiles"

    @property
    def manifest(self) -> Path:
        return self.profiles_repo / "manifest.json"

    @property
    def profile_state(self) -> Path:
        return self.profiles_repo / "profile-state.json"

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
    def archives(self) -> Path:
        return self.profile_dir / "archives.txt"

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
