"""QA 結果狀態、例外與 step 共用欄位。"""

from __future__ import annotations




PASS, FAIL, HANDOFF, SKIPPED = "pass", "fail", "handoff", "skipped"


class ConfigError(Exception):
    """The qa.json is wrong — caught before anything is touched."""


class StepFailed(Exception):
    def __init__(self, message: str, failures: list | None = None):
        super().__init__(message)
        self.failures = failures or []


def require(step: dict, key: str):
    if key not in step:
        raise ConfigError(f"step {step.get('type')!r} needs `{key}`")
    return step[key]


def describe(step: dict) -> str:
    kind = step.get("type", "?")
    for key in ("cmd", "name", "form_id", "message", "text", "info_form_id", "index", "editor_id",
                "mod_name", "source", "message", "save", "seconds"):
        if key in step:
            return f"{kind}: {step[key]}"
    return kind
