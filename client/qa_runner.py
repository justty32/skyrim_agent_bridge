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
import configparser
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import bridge
import mo2ctl

from qa_runner_baseline import (  # noqa: F401
    active_baseline_context, preflight_baseline,
)
from qa_runner_baseline_rules import (  # noqa: F401
    BASELINE_EXTENSIONS, BASELINE_FORMAT, BASELINE_SIGNATURE_FIELDS, BASELINE_TRUSTED_ROOT_ENV,
    _SAVE_STEM, _SHA256, _form_id, _load_baseline_timings, _manifest_path, _sha256_file,
    _validate_state_signature,
)
from qa_runner_common import (  # noqa: F401
    ConfigError, FAIL, HANDOFF, PASS, SKIPPED, StepFailed, describe, require,
)
from qa_runner_conditions import (  # noqa: F401
    COUNT_OPS, NEGATIVE_OPS, OPS, SET_OPS, _INDEX, _SEGMENT, _contains, _num, check, resolve,
)
from qa_runner_fingerprint import (  # noqa: F401
    check_state_signature, poll_state_signature, state_load_epoch,
)
from qa_runner_report import (  # noqa: F401
    MARK, render,
)
from qa_runner_validation import (  # noqa: F401
    KNOWN_TYPES, validate,
)


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
    def __init__(self, spec: dict, base_dir: Path, *, interactive: bool,
                 clock=time.monotonic, sleeper=time.sleep,
                 baseline_context=active_baseline_context):
        self.spec = spec
        self.base_dir = base_dir
        self.interactive = interactive
        self.defaults = spec.get("defaults", {})
        self.clock = clock
        self.sleeper = sleeper
        self.baseline_context = baseline_context

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
                      wait=budget, no_wait=False,
                      background_active=step.get("background_active", True))
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
        try:
            timings = _load_baseline_timings(step, self.defaults)
        except ConfigError as exc:
            raise StepFailed(f"invalid load_baseline: {exc}") from exc
        try:
            context = self.baseline_context()
            preflight = preflight_baseline(
                self.spec, self.base_dir, active_context=context
            )
        except ConfigError as exc:
            raise StepFailed(f"baseline preflight failed: {exc}") from exc
        save = preflight["stem"]
        if step.get("save") is not None and step["save"] != save:
            raise StepFailed(
                f"load_baseline `save` {step['save']!r} does not match manifest stem {save!r}"
            )
        before = bridge.state(timeout=timings["state_timeout"])
        if not before.get("ok"):
            raise StepFailed(
                f"cannot establish pre-load state epoch: {before.get('error')}"
            )
        try:
            before_epoch = state_load_epoch(before)
        except ConfigError as exc:
            raise StepFailed(f"cannot establish pre-load state epoch: {exc}") from exc
        result = bridge.console(f"load {save}", timeout=timings["timeout"])
        if not result.get("ok"):
            raise StepFailed(f"load failed: {result.get('error')}")
        # Console success only proves that the command was accepted. A save load
        # is asynchronous and may stop at a modal, so prove the complete runtime
        # identity instead of sleeping and declaring success.
        fingerprint = poll_state_signature(
            lambda: bridge.state(timeout=timings["state_timeout"]),
            preflight["state_signature"],
            timings["retry_for"],
            timings["retry_interval"],
            before_epoch,
            clock=self.clock,
            sleeper=self.sleeper,
        )
        return {
            "save": save,
            **result,
            "baseline_preflight": {
                key: value for key, value in preflight.items() if key != "state_signature"
            },
            "state_fingerprint": fingerprint,
        }

    def step_console(self, step) -> dict:
        result = bridge.console(require(step, "cmd"), step.get("ref"),
                                timeout=step.get("timeout", 30.0))
        if not result.get("ok"):
            raise StepFailed(f"console call failed: {result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_move_to_actor(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.move_to_actor(
                step.get("name"), form_id=step.get("form_id"),
                scope=step.get("scope", "cell"), distance=step.get("distance", 128.0),
                timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            raise StepFailed(f"move-to-actor failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_activate_actor(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.activate_actor(
                step.get("name"), form_id=step.get("form_id"),
                scope=step.get("scope", "cell"), timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            raise StepFailed(f"activate-actor failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_select_dialogue(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.select_dialogue(
                step.get("text"), contains=step.get("contains", False),
                index=step.get("index"), info_form_id=step.get("info_form_id"),
                timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            available = result.get("available") or []
            suffix = f"; available={available}" if available else ""
            raise StepFailed(f"select-dialogue failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}{suffix}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_close_dialogue(self, step) -> dict:
        result = bridge.close_dialogue(timeout=step.get("timeout", 20.0))
        if not result.get("ok"):
            raise StepFailed(f"close-dialogue failed: {result.get('error')}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_select_message_box(self, step) -> dict:
        result = retry_for_ok(
            lambda: bridge.select_message_box(
                step.get("text"), index=step.get("index"), message=step.get("message"),
                timeout=step.get("timeout", 20.0)),
            step.get("retry_for", 0), step.get("retry_interval", 1.0))
        if not result.get("ok"):
            available = result.get("available") or []
            suffix = f"; available={available}" if available else ""
            raise StepFailed(f"select-message-box failed after {result['attempts']} attempt(s): "
                             f"{result.get('error')}{suffix}")
        settle = step.get("settle", self.defaults.get("settle_seconds", 0))
        if settle:
            time.sleep(settle)
        return result

    def step_assert_global(self, step) -> dict:
        editor_id = require(step, "editor_id")
        condition = require(step, "expect")
        budget = step.get("retry_for", self.defaults.get("assert_retry_seconds", 20))
        interval = step.get("retry_interval", 2.0)
        deadline = time.time() + budget
        attempts = 0
        while True:
            attempts += 1
            snapshot = bridge.global_value(editor_id, timeout=step.get("timeout", 20.0))
            if snapshot.get("ok"):
                failure = check(snapshot, "value", condition)
                if not failure:
                    return {"editor_id": editor_id, "value": snapshot["value"],
                            "attempts": attempts}
                error = "global expectation failed"
            else:
                failure = None
                error = f"global unavailable: {snapshot.get('error')}"
            if time.time() >= deadline:
                raise StepFailed(f"{error} (after {attempts} attempt(s) over {budget}s)",
                                 [failure] if failure else [])
            time.sleep(interval)

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
        except Exception as exc:
            return {**record, "status": FAIL, "duration_s": round(time.time() - started, 1),
                    "error": str(exc)}

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


def retry_for_ok(probe, timeout: float, interval: float = 1.0) -> dict:
    """Retry a semantic action until it succeeds; always make at least one attempt."""
    deadline = time.time() + max(0, timeout)
    attempts = 0
    while True:
        attempts += 1
        result = probe()
        if result.get("ok") or time.time() >= deadline:
            return {**result, "attempts": attempts}
        time.sleep(interval)


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
