#!/usr/bin/env python3
"""qa_runner — execute a qa.json and report pass/fail per step.

Phase 3.2 of the AI QA loop. Ties `mo2ctl` (install, launch, kill) to the in-game
bridge (`state`, `console`) so one file describes a whole test run and one command
executes it.

  qa_runner.py <file.qa.json> [--json] [--dry-run] [--keep-going]

Exit codes: 0 all passed, 1 something failed, 2 passed but needs a human to look,
3 the qa.json itself is wrong.

Schema: QA-SCHEMA.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bridge
import mo2ctl

PASS, FAIL, HANDOFF, SKIPPED = "pass", "fail", "handoff", "skipped"


class ConfigError(Exception):
    """The qa.json is wrong — caught before anything is touched."""


class StepFailed(Exception):
    def __init__(self, message: str, failures: list | None = None):
        super().__init__(message)
        self.failures = failures or []


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


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _ns(**kwargs) -> SimpleNamespace:
    """mo2ctl's commands take an argparse Namespace; build one directly.

    Cheaper than shelling out per step, and it keeps mo2ctl's `Fail` messages
    intact instead of reducing them to an exit code.
    """
    return SimpleNamespace(**kwargs)


def _mo2(fn, **kwargs) -> dict:
    try:
        return fn(mo2ctl.load_env(), _ns(**kwargs))
    except mo2ctl.Fail as exc:
        raise StepFailed(str(exc)) from exc


class Runner:
    def __init__(self, spec: dict, base_dir: Path, *, interactive: bool):
        self.spec = spec
        self.base_dir = base_dir
        self.interactive = interactive
        self.defaults = spec.get("defaults", {})

    def path_of(self, value: str) -> Path:
        """Resolve a step's path relative to the qa.json, not the shell's cwd.

        A test file that only works when you run it from one directory is a test
        file that stops working the moment anything automates it.
        """
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    # -- individual step types ------------------------------------------------

    def step_install(self, step) -> dict:
        source = self.path_of(require(step, "source"))
        if not source.exists():
            raise StepFailed(f"source does not exist: {source}")
        return _mo2(mo2ctl.cmd_install,
                    source=str(source),
                    name=step.get("mod_name"),
                    no_enable=not step.get("enable", True),
                    force=step.get("force", True),
                    version=step.get("version", "0.0.0"),
                    comment=step.get("comment", "Installed by qa_runner. TEST HARNESS."))

    def step_uninstall(self, step) -> dict:
        return _mo2(mo2ctl.cmd_uninstall, name=require(step, "mod_name"),
                    keep_files=step.get("keep_files", False), force=step.get("force", False))

    def step_enable(self, step) -> dict:
        return _mo2(mo2ctl.cmd_enable, name=require(step, "mod_name"), force=step.get("force", False))

    def step_disable(self, step) -> dict:
        return _mo2(mo2ctl.cmd_disable, name=require(step, "mod_name"), force=step.get("force", False))

    def step_launch(self, step) -> dict:
        budget = step.get("wait", 240.0)
        started = time.time()
        result = _mo2(mo2ctl.cmd_launch, shortcut=step.get("shortcut", "SKSE"),
                      wait=budget, no_wait=False)
        if not result.get("bridge", {}).get("reachable"):
            raise StepFailed(f"bridge never answered: {result.get('bridge', {}).get('error')}")

        # /ping answers on the socket thread and keeps answering through load
        # screens — by design, so a runner can tell "process alive, game busy"
        # from "process dead". It therefore does NOT mean the game thread is
        # draining tasks yet, and the first /state after launch reliably 503s.
        # Everything downstream asserts on state, so wait for the real thing.
        remaining = max(10.0, budget - (time.time() - started))
        snapshot = wait_for(lambda: bridge.state(timeout=10.0), remaining)
        if not snapshot.get("ok"):
            raise StepFailed(f"bridge is up but the game thread never answered "
                             f"within {remaining:.0f}s: {snapshot.get('error')}")
        result["state_ready_after_s"] = round(time.time() - started, 1)
        return result

    def step_kill(self, step) -> dict:
        return _mo2(mo2ctl.cmd_kill, mo2=step.get("mo2", False), timeout=step.get("timeout", 15.0))

    def step_load_baseline(self, step) -> dict:
        save = step.get("save") or self.spec.get("baseline")
        if not save:
            raise StepFailed("no save named, and the spec has no top-level `baseline`")
        result = bridge.console(f"load {save}", timeout=step.get("timeout", 60.0))
        if not result.get("ok"):
            raise StepFailed(f"load failed: {result.get('error')}")
        # Loading is asynchronous — the console call returns long before the cell
        # is up. Everything downstream asserts on state, so settle here.
        time.sleep(step.get("settle", self.defaults.get("settle_seconds", 8)))
        return {"save": save, **result}

    def step_console(self, step) -> dict:
        result = bridge.console(require(step, "cmd"), step.get("ref"),
                                timeout=step.get("timeout", 30.0))
        if not result.get("ok"):
            raise StepFailed(f"console call failed: {result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_wait(self, step) -> dict:
        seconds = step.get("seconds", 1)
        time.sleep(seconds)
        return {"waited": seconds}

    def step_assert_state(self, step) -> dict:
        expect = step.get("expect") or {}
        if not expect:
            raise ConfigError("assert_state needs a non-empty `expect`")

        # Assert-eventually, not assert-now. Nearly everything the game does in
        # response to a console command is asynchronous — `coc` returns before
        # the cell is loaded, an actor value takes a frame to propagate — so a
        # single-shot check turns "correct but not yet" into a failure. Retrying
        # a *passing* condition costs one request; not retrying costs a false
        # red on every timing-sensitive step. Set `retry_for: 0` to assert now.
        budget = step.get("retry_for", self.defaults.get("assert_retry_seconds", 20))
        interval = step.get("retry_interval", 2.0)
        deadline = time.time() + budget
        attempts = 0
        while True:
            attempts += 1
            snapshot = bridge.state(step.get("include"), radius=step.get("radius"),
                                    limit=step.get("limit"), timeout=step.get("timeout", 20.0))
            if snapshot.get("ok"):
                failures = [f for f in (check(snapshot, p, c) for p, c in expect.items()) if f]
                if not failures:
                    return {"checked": len(expect), "attempts": attempts}
                error = f"{len(failures)}/{len(expect)} expectation(s) failed"
            else:
                failures = []
                error = f"/state unavailable: {snapshot.get('error')}"

            if time.time() >= deadline:
                raise StepFailed(f"{error} (after {attempts} attempt(s) over {budget}s)", failures)
            time.sleep(interval)

    def step_handoff_user(self, step) -> dict:
        message = require(step, "message")
        if not self.interactive:
            # Not a terminal: whoever invoked this (an agent, CI) relays the
            # message. Per plan decision D6 the run does not try to substitute
            # for the human — it records what needs looking at and moves on,
            # and the overall status becomes needs_human.
            return {"handoff": True, "message": message, "expect": step.get("expect")}
        print(f"\n>>> {message}")
        if step.get("expect"):
            print(f"    expected: {step['expect']}")
        answer = input("    [Enter]=looks right, or type what's wrong: ").strip()
        if answer:
            raise StepFailed(f"user reported: {answer}")
        return {"handoff": True, "message": message, "confirmed_by_user": True}

    # -- driving --------------------------------------------------------------

    def run(self) -> dict:
        started = time.time()
        results: list[dict] = []
        steps = self.spec.get("steps", [])
        teardown = self.spec.get("teardown", [])

        stop_at = None
        for index, step in enumerate(steps):
            if stop_at is not None:
                results.append(self._skipped(index, step))
                continue
            outcome = self._run_one(index, step)
            results.append(outcome)
            if outcome["status"] == FAIL and not step.get("continue_on_fail"):
                stop_at = index

        # Teardown always runs. The whole point of a repeatable loop is that a
        # failure in the middle still leaves the profile the way it was found;
        # a run that fails at step 3 and leaves a test mod installed poisons
        # every run after it.
        for index, step in enumerate(teardown):
            results.append({**self._run_one(index, step), "phase": "teardown"})

        counts = {s: sum(1 for r in results if r["status"] == s) for s in (PASS, FAIL, HANDOFF, SKIPPED)}
        status = FAIL if counts[FAIL] else ("needs_human" if counts[HANDOFF] else PASS)
        return {
            "name": self.spec.get("name", "unnamed"),
            "status": status,
            "duration_s": round(time.time() - started, 1),
            "counts": counts,
            "steps": results,
            "handoffs": [r["detail"]["message"] for r in results
                         if r["status"] == HANDOFF and "message" in r.get("detail", {})],
        }

    def _run_one(self, index: int, step: dict) -> dict:
        kind = step.get("type")
        handler = getattr(self, f"step_{kind}", None)
        label = step.get("label") or describe(step)
        record = {"index": index, "type": kind, "label": label}
        if handler is None:
            return {**record, "status": FAIL, "duration_s": 0.0,
                    "error": f"unknown step type: {kind!r}"}

        started = time.time()
        try:
            detail = handler(step)
            status = HANDOFF if detail.get("handoff") and not detail.get("confirmed_by_user") else PASS
            return {**record, "status": status, "duration_s": round(time.time() - started, 1),
                    "detail": detail}
        except StepFailed as exc:
            return {**record, "status": FAIL, "duration_s": round(time.time() - started, 1),
                    "error": str(exc), "failures": exc.failures}

    @staticmethod
    def _skipped(index: int, step: dict) -> dict:
        return {"index": index, "type": step.get("type"),
                "label": step.get("label") or describe(step),
                "status": SKIPPED, "duration_s": 0.0}


def wait_for(probe, timeout: float, interval: float = 2.0) -> dict:
    """Poll `probe` until it returns a dict with ok=True, or the budget runs out."""
    deadline = time.time() + timeout
    while True:
        result = probe()
        if result.get("ok") or time.time() >= deadline:
            return result
        time.sleep(interval)


def require(step: dict, key: str):
    if key not in step:
        raise ConfigError(f"step {step.get('type')!r} needs `{key}`")
    return step[key]


def describe(step: dict) -> str:
    kind = step.get("type", "?")
    for key in ("cmd", "mod_name", "source", "message", "save", "seconds"):
        if key in step:
            return f"{kind}: {step[key]}"
    return kind


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

KNOWN_TYPES = {"install", "uninstall", "enable", "disable", "launch", "kill",
               "load_baseline", "console", "wait", "assert_state", "handoff_user"}


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
            if kind == "handoff_user" and not step.get("message"):
                problems.append(f"{where}: handoff_user needs `message`")
            if kind == "load_baseline" and not (step.get("save") or spec.get("baseline")):
                problems.append(f"{where}: no `save` and no top-level `baseline`")
            if kind == "assert_state":
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


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

MARK = {PASS: "  ok  ", FAIL: " FAIL ", HANDOFF: " look ", SKIPPED: " skip "}


def render(report: dict) -> str:
    lines = [f"{report['name']} — {report['status'].upper()} in {report['duration_s']}s"]
    for step in report["steps"]:
        tail = f" ({step['duration_s']}s)" if step["duration_s"] else ""
        phase = " [teardown]" if step.get("phase") == "teardown" else ""
        lines.append(f"[{MARK[step['status']]}] {step['label']}{tail}{phase}")
        if step.get("error"):
            lines.append(f"          {step['error']}")
        for failure in step.get("failures", []):
            lines.append(f"          {failure['path']} {failure['op']} {failure['expected']!r}"
                         f" — actual: {failure['actual']!r}")
    if report["handoffs"]:
        lines.append("\nNeeds a human to look at:")
        lines.extend(f"  - {m}" for m in report["handoffs"])
    counts = report["counts"]
    lines.append(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, "
                 f"{counts[HANDOFF]} for review, {counts[SKIPPED]} skipped")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="qa_runner", description=__doc__.splitlines()[0])
    parser.add_argument("spec", help="path to a .qa.json")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--dry-run", action="store_true", help="validate only; touch nothing")
    parser.add_argument("--no-interactive", action="store_true",
                        help="never prompt on handoff_user (default when stdin isn't a tty)")
    parser.add_argument("--report", help="also write the JSON report here")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec).expanduser().resolve()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"qa_runner: cannot read {spec_path}: {exc}", file=sys.stderr)
        return 3

    problems = validate(spec, spec_path.parent)
    if problems:
        print(f"qa_runner: {spec_path.name} is not valid:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 3
    if args.dry_run:
        total = len(spec.get("steps", [])) + len(spec.get("teardown", []))
        print(f"{spec.get('name', spec_path.stem)}: valid, {total} step(s)")
        return 0

    interactive = sys.stdin.isatty() and not args.no_interactive
    report = Runner(spec, spec_path.parent, interactive=interactive).run()

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False) if args.json else render(report))

    return {PASS: 0, FAIL: 1, "needs_human": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
