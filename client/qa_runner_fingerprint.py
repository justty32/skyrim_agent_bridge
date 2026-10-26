"""Runtime load epoch 與 state fingerprint 輪詢。"""

from __future__ import annotations

import time

from qa_runner_baseline_rules import (  # noqa: F401
    _form_id,
)
from qa_runner_common import (  # noqa: F401
    ConfigError, StepFailed,
)
from qa_runner_conditions import (  # noqa: F401
    resolve,
)


def check_state_signature(snapshot: dict, signature: dict) -> tuple[list[dict], dict]:
    """Compare exact runtime identity fields, normalising FormID representations."""
    failures = []
    actual = {}
    for path, expected in signature.items():
        values, multi = resolve(snapshot, path)
        actual[path] = values[0] if len(values) == 1 else values
        if path == "player.cell_form_id":
            expected_value = _form_id(expected, "state_signature `player.cell_form_id`")
            converted = []
            for value in values:
                try:
                    converted.append(_form_id(value, "runtime `player.cell_form_id`"))
                except ConfigError:
                    pass
            ok = expected_value in converted
        else:
            # Python considers 0 == False and 1 == True. JSON state does not:
            # a numeric modal/dead/interior flag is a malformed bridge response,
            # not a valid match for a boolean fingerprint field.
            ok = bool(values) and any(
                value == expected and
                (not isinstance(expected, bool) or isinstance(value, bool))
                for value in values
            )
        if not ok:
            failures.append({
                "path": path,
                "op": "eq",
                "expected": expected,
                "actual": values,
                "matched_any_of": len(values) if multi else None,
            })
    return failures, actual


def state_load_epoch(snapshot: dict) -> int:
    values, _multi = resolve(snapshot, "game.load_epoch")
    if (len(values) != 1 or isinstance(values[0], bool) or
            not isinstance(values[0], int) or values[0] < 0):
        raise ConfigError(
            "runtime /state must expose one non-negative integer `game.load_epoch`; "
            "rebuild and deploy the matching AgentBridge DLL"
        )
    return values[0]


def poll_state_signature(probe, signature: dict, timeout: float, interval: float,
                         after_load_epoch: int,
                         *, clock=time.monotonic, sleeper=time.sleep) -> dict:
    """Poll structured state until the complete fingerprint converges or times out."""
    deadline = clock() + max(0, timeout)
    attempts = 0
    failures = []
    actual = {}
    error = "/state was not queried"
    while True:
        attempts += 1
        snapshot = probe()
        if snapshot.get("ok"):
            failures, actual = check_state_signature(snapshot, signature)
            try:
                load_epoch = state_load_epoch(snapshot)
            except ConfigError:
                load_epoch = None
            actual["game.load_epoch"] = load_epoch
            if load_epoch is None or load_epoch <= after_load_epoch:
                failures.append({
                    "path": "game.load_epoch",
                    "op": "gt",
                    "expected": after_load_epoch,
                    "actual": [] if load_epoch is None else [load_epoch],
                    "matched_any_of": None,
                })
            if not failures:
                return {
                    "matched": True,
                    "attempts": attempts,
                    "expected": signature,
                    "actual": actual,
                    "load_epoch": {"before": after_load_epoch, "after": load_epoch},
                }
            error = f"baseline state fingerprint did not match ({len(failures)} field(s))"
        else:
            failures = []
            actual = {}
            error = f"/state unavailable: {snapshot.get('error')}"
        if clock() >= deadline:
            raise StepFailed(
                f"{error} (after {attempts} attempt(s) over {timeout}s)", failures
            )
        sleeper(interval)
