"""qa.json 離線驗證。"""

from __future__ import annotations

from pathlib import Path

from qa_runner_baseline import (  # noqa: F401
    preflight_baseline,
)
from qa_runner_baseline_rules import (  # noqa: F401
    _load_baseline_timings,
)
from qa_runner_common import (  # noqa: F401
    ConfigError,
)
from qa_runner_conditions import (  # noqa: F401
    COUNT_OPS, OPS, SET_OPS, resolve,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

KNOWN_TYPES = {"install", "uninstall", "enable", "disable", "launch", "kill",
               "load_baseline", "console", "wait", "assert_state", "assert_global",
               "move_to_actor", "activate_actor", "select_dialogue", "close_dialogue",
               "select_message_box",
               "handoff_user"}


def validate(spec: dict, base_dir: Path) -> list[str]:
    """Catch everything checkable without touching MO2 or the game.

    Worth doing eagerly: the expensive part of a run is a game launch, and
    finding out at step 9 that step 12 has a typo wastes the whole thing.
    """
    problems = []
    if not isinstance(spec.get("steps"), list) or not spec["steps"]:
        problems.append("`steps` must be a non-empty list")
        return problems

    for phase in ("steps", "teardown"):
        for index, step in enumerate(spec.get(phase) or []):
            where = f"{phase}[{index}]"
            if not isinstance(step, dict):
                problems.append(f"{where}: not an object")
                continue
            kind = step.get("type")
            if kind not in KNOWN_TYPES:
                problems.append(f"{where}: unknown type {kind!r} (known: {', '.join(sorted(KNOWN_TYPES))})")
                continue
            if kind == "install":
                source = step.get("source")
                if not source:
                    problems.append(f"{where}: install needs `source`")
                else:
                    path = Path(source).expanduser()
                    path = path if path.is_absolute() else (base_dir / path)
                    if not path.exists():
                        problems.append(f"{where}: source not found: {path}")
            if kind in ("uninstall", "enable", "disable") and not step.get("mod_name"):
                problems.append(f"{where}: {kind} needs `mod_name`")
            if kind == "console" and not step.get("cmd"):
                problems.append(f"{where}: console needs `cmd`")
            if kind in ("move_to_actor", "activate_actor"):
                selectors = [key for key in ("name", "form_id") if step.get(key) is not None]
                if len(selectors) != 1:
                    problems.append(f"{where}: {kind} needs exactly one of `name` or `form_id`")
                if step.get("scope", "cell") not in ("cell", "loaded"):
                    problems.append(f"{where}: {kind} scope must be `cell` or `loaded`")
            if kind == "select_dialogue":
                selectors = [key for key in ("text", "index", "info_form_id")
                             if step.get(key) is not None]
                if len(selectors) != 1:
                    problems.append(f"{where}: select_dialogue needs exactly one of "
                                    "`text`, `index`, or `info_form_id`")
            if kind == "select_message_box":
                selectors = [key for key in ("text", "index") if step.get(key) is not None]
                if len(selectors) != 1:
                    problems.append(f"{where}: select_message_box needs exactly one of "
                                    "`text` or `index`")
                elif selectors[0] == "text" and not step["text"]:
                    problems.append(f"{where}: select_message_box `text` must not be empty")
                elif selectors[0] == "index" and (isinstance(step["index"], bool) or
                                                   not isinstance(step["index"], int) or
                                                   step["index"] < 0):
                    problems.append(f"{where}: select_message_box `index` must be a "
                                    "non-negative integer")
            if kind == "assert_global":
                if not step.get("editor_id"):
                    problems.append(f"{where}: assert_global needs `editor_id`")
                condition = step.get("expect")
                if not isinstance(condition, dict) or len(condition) != 1:
                    problems.append(f"{where}: assert_global needs one comparison in `expect`")
                elif next(iter(condition)) not in OPS and next(iter(condition)) not in SET_OPS:
                    problems.append(f"{where}: assert_global has unknown operator {next(iter(condition))!r}")
            if kind == "handoff_user" and not step.get("message"):
                problems.append(f"{where}: handoff_user needs `message`")
            if kind == "load_baseline":
                try:
                    _load_baseline_timings(step, spec.get("defaults", {}))
                except ConfigError as exc:
                    problems.append(f"{where}: {exc}")
                try:
                    preflight = preflight_baseline(spec, base_dir)
                    if step.get("save") is not None and step["save"] != preflight["stem"]:
                        problems.append(
                            f"{where}: `save` {step['save']!r} does not match manifest stem "
                            f"{preflight['stem']!r}"
                        )
                except ConfigError as exc:
                    problems.append(f"{where}: baseline preflight failed: {exc}")
            if kind == "assert_state":
                include = step.get("include")
                if ("include" in step
                        and (not isinstance(include, list)
                             or any(not isinstance(item, str) for item in include))):
                    problems.append(f"{where}: include must be a list of strings")
                expect = step.get("expect")
                if not isinstance(expect, dict) or not expect:
                    problems.append(f"{where}: assert_state needs a non-empty `expect` object")
                    continue
                for path, condition in expect.items():
                    if isinstance(condition, dict):
                        if len(condition) != 1:
                            problems.append(f"{where}: {path}: needs exactly one operator")
                            continue
                        op = next(iter(condition))
                        if op not in OPS and op not in COUNT_OPS and op not in SET_OPS:
                            problems.append(f"{where}: {path}: unknown operator {op!r}")
                    try:
                        resolve({}, path)
                    except ConfigError as exc:
                        problems.append(f"{where}: {exc}")
    return problems
