"""JSON path 解析與條件比較。"""

from __future__ import annotations

import re

from qa_runner_common import (  # noqa: F401
    ConfigError,
)


# ---------------------------------------------------------------------------
# Path resolution
#
# Dotted paths into the /state JSON, with `[*]` for "every element" and `[N]` for
# one. `plugins[*].name` is the common case: it resolves to a list, and the
# comparison then asks whether ANY element satisfies it.
# ---------------------------------------------------------------------------

_SEGMENT = re.compile(r"^([^\[\]]*)((?:\[[^\[\]]+\])*)$")
_INDEX = re.compile(r"\[([^\[\]]+)\]")


def resolve(data, path: str) -> tuple[list, bool]:
    """Return (values, multi). `multi` records whether a `[*]` widened the path."""
    values = [data]
    multi = False
    for raw in path.split("."):
        match = _SEGMENT.match(raw)
        if not match:
            raise ConfigError(f"bad path segment: {raw!r} in {path!r}")
        key, indices = match.group(1), _INDEX.findall(match.group(2))

        if key:
            values = [v[key] for v in values if isinstance(v, dict) and key in v]
        for index in indices:
            widened = []
            for value in values:
                if not isinstance(value, list):
                    continue
                if index == "*":
                    widened.extend(value)
                    multi = True
                else:
                    try:
                        widened.append(value[int(index)])
                    except (ValueError, IndexError):
                        pass
            values = widened
    return values, multi


# ---------------------------------------------------------------------------
# Comparisons
#
# Positive operators pass when ANY resolved value satisfies them; negative ones
# (`ne`, `not_contains`) require ALL of them to. That is how the words read:
# "plugins[*].name not_contains Foo" means no plugin matches, not "some plugin
# doesn't".
# ---------------------------------------------------------------------------


def _contains(haystack, needle) -> bool:
    if isinstance(haystack, str):
        return str(needle) in haystack
    if isinstance(haystack, (list, tuple, dict)):
        return needle in haystack
    return False


OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "gte": lambda a, b: _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "contains": _contains,
    "not_contains": lambda a, b: not _contains(a, b),
    "matches": lambda a, b: re.search(str(b), str(a)) is not None,
}
NEGATIVE_OPS = {"ne", "not_contains"}
COUNT_OPS = {"count_eq", "count_gte", "count_lte"}
SET_OPS = {"exists"}


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(f"not comparable as a number: {value!r}")
    return float(value)


def check(data, path: str, condition) -> dict | None:
    """Evaluate one expectation. Returns a failure dict, or None when it holds."""
    if not isinstance(condition, dict):
        condition = {"eq": condition}
    if len(condition) != 1:
        raise ConfigError(f"expectation for {path!r} needs exactly one operator, got {list(condition)}")
    (op, expected), = condition.items()

    values, multi = resolve(data, path)

    if op in SET_OPS:
        ok = bool(values) == bool(expected)
    elif op in COUNT_OPS:
        count = len(values)
        ok = {"count_eq": count == expected,
              "count_gte": count >= expected,
              "count_lte": count <= expected}[op]
    elif op not in OPS:
        raise ConfigError(f"unknown operator {op!r} for {path!r}")
    elif not values:
        ok = False  # nothing there to satisfy anything, negatives included
    elif op in NEGATIVE_OPS:
        ok = all(OPS[op](v, expected) for v in values)
    else:
        ok = any(OPS[op](v, expected) for v in values)

    if ok:
        return None
    # Long paths like plugins[*].name resolve to 60 entries; a failure report that
    # dumps all of them buries the point.
    actual = values if len(values) <= 8 else values[:8] + [f"... +{len(values) - 8} more"]
    return {"path": path, "op": op, "expected": expected,
            "actual": actual, "matched_any_of": len(values) if multi else None}
