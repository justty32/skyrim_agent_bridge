#!/usr/bin/env python3

import hashlib
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_runner


def write_baseline_manifest(root: Path, *, ess=b"ess-save", skse=b"skse-cosave",
                            state_signature: dict | None = None) -> Path:
    stem = "Baseline0A"
    (root / f"{stem}.ess").write_bytes(ess)
    (root / f"{stem}.skse").write_bytes(skse)
    signature = state_signature or {
        "player.name": "Prisoner",
        "player.cell_form_id": "0x0001A26F",
        "player.interior": False,
        "player.flags.dead": False,
        "game.message_box.open": False,
    }
    manifest = {
        "format": "baseline-manifest-v1",
        "profile": "QaProfile",
        "isolation": {
            "settings_ini": {"LocalSaves": "true", "LocalSettings": "true"},
            "skyrimcustom_ini": {"sLocalSavePath": "__MO_Saves\\"},
        },
        "pair": {
            "stem": stem,
            "directory": str(root),
            "members": [
                {
                    "extension": extension,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for extension, content in ((".ess", ess), (".skse", skse))
            ],
        },
        "state_signature": signature,
    }
    path = root / "baseline-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def matching_state(**overrides) -> dict:
    state = {
        "ok": True,
        "player": {
            "name": "Prisoner",
            "cell_form_id": 0x0001A26F,
            "interior": False,
            "flags": {"dead": False},
        },
        "game": {"load_epoch": 1, "message_box": {"open": False}},
    }
    for path, value in overrides.items():
        target = state
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return state


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class BaselineLoadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tempdir.name)
        self.manifest = write_baseline_manifest(self.base_dir)
        self.env_patch = patch.dict(
            os.environ,
            {qa_runner.BASELINE_TRUSTED_ROOT_ENV: str(self.base_dir)},
        )
        self.env_patch.start()
        self.spec = {"baseline": {"manifest": str(self.manifest)}, "steps": []}
        self.fake_time = FakeTime()
        self.runner = qa_runner.Runner(
            self.spec,
            self.base_dir,
            interactive=False,
            clock=self.fake_time.clock,
            sleeper=self.fake_time.sleep,
            baseline_context=lambda: {
                "profile": "QaProfile", "save_directory": self.base_dir,
            },
        )

    def tearDown(self):
        self.env_patch.stop()
        self.tempdir.cleanup()

    def test_preflight_verifies_exact_pair_and_hashes(self):
        result = qa_runner.preflight_baseline(self.spec, self.base_dir)

        self.assertEqual(result["stem"], "Baseline0A")
        self.assertEqual([m["extension"] for m in result["members"]], [".ess", ".skse"])
        self.assertEqual(
            result["members"][0]["sha256"], hashlib.sha256(b"ess-save").hexdigest()
        )

    def test_manifest_backed_load_validates_for_dry_run(self):
        spec = {**self.spec, "steps": [{"type": "load_baseline"}]}

        self.assertEqual(qa_runner.validate(spec, self.base_dir), [])

    def test_relative_manifest_path_is_rejected(self):
        spec = {
            "baseline": {"manifest": self.manifest.name},
            "steps": [{"type": "load_baseline"}],
        }

        problems = qa_runner.validate(spec, self.base_dir)

        self.assertIn("absolute external path", problems[0])

    def test_manifest_must_be_inside_deployment_trusted_root(self):
        with patch.dict(
            os.environ,
            {qa_runner.BASELINE_TRUSTED_ROOT_ENV: str(self.base_dir / "elsewhere")},
        ):
            problems = qa_runner.validate(
                {**self.spec, "steps": [{"type": "load_baseline"}]}, self.base_dir
            )

        self.assertIn("outside trusted root", problems[0])

    @patch("qa_runner.bridge.console")
    def test_missing_cosave_fails_before_console_load(self, console):
        (self.base_dir / "Baseline0A.skse").unlink()

        with self.assertRaisesRegex(qa_runner.StepFailed, "baseline member is missing"):
            self.runner.step_load_baseline({"type": "load_baseline"})

        console.assert_not_called()

    @patch("qa_runner.bridge.console")
    def test_hash_mismatch_fails_before_console_load(self, console):
        (self.base_dir / "Baseline0A.ess").write_bytes(b"changed!")

        with self.assertRaisesRegex(qa_runner.StepFailed, "SHA-256 mismatch"):
            self.runner.step_load_baseline({"type": "load_baseline"})

        console.assert_not_called()

    @patch("qa_runner.bridge.console")
    def test_invalid_poll_interval_fails_before_console_load(self, console):
        with self.assertRaisesRegex(qa_runner.StepFailed, "retry_interval.*positive"):
            self.runner.step_load_baseline({
                "type": "load_baseline", "retry_interval": -1,
            })

        console.assert_not_called()

    @patch("qa_runner.bridge.console")
    def test_manifest_directory_must_be_active_profile_save_directory(self, console):
        runner = qa_runner.Runner(
            self.spec,
            self.base_dir,
            interactive=False,
            baseline_context=lambda: {
                "profile": "QaProfile", "save_directory": self.base_dir / "other-saves",
            },
        )

        with self.assertRaisesRegex(qa_runner.StepFailed, "not the active profile save"):
            runner.step_load_baseline({"type": "load_baseline"})

        console.assert_not_called()

    @patch("qa_runner.mo2ctl.read_selected_profile", return_value="QaProfile")
    @patch("qa_runner.mo2ctl.load_env")
    def test_active_context_requires_local_saves(self, load_env, _selected):
        profile_dir = self.base_dir / "profiles" / "QaProfile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "settings.ini").write_text(
            "[General]\nLocalSaves=false\n", encoding="utf-8"
        )
        load_env.return_value = qa_runner.SimpleNamespace(
            root=self.base_dir, profile="QaProfile", profile_dir=profile_dir
        )

        with self.assertRaisesRegex(qa_runner.ConfigError, "LocalSaves=true"):
            qa_runner.active_baseline_context()

    @patch("qa_runner.mo2ctl.read_selected_profile", return_value="QaProfile")
    @patch("qa_runner.mo2ctl.load_env")
    def test_active_context_requires_local_settings(self, load_env, _selected):
        profile_dir = self.base_dir / "profiles" / "QaProfile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "settings.ini").write_text(
            "[General]\nLocalSaves=true\nLocalSettings=false\n", encoding="utf-8"
        )
        load_env.return_value = qa_runner.SimpleNamespace(
            root=self.base_dir, profile="QaProfile", profile_dir=profile_dir
        )

        with self.assertRaisesRegex(qa_runner.ConfigError, "LocalSettings=true"):
            qa_runner.active_baseline_context()

    @patch("qa_runner.mo2ctl.read_selected_profile", return_value="QaProfile")
    @patch("qa_runner.mo2ctl.load_env")
    def test_active_context_requires_mo_local_save_path(self, load_env, _selected):
        profile_dir = self.base_dir / "profiles" / "QaProfile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "settings.ini").write_text(
            "[General]\nLocalSaves=true\nLocalSettings=true\n", encoding="utf-8"
        )
        (profile_dir / "skyrimcustom.ini").write_text(
            "[General]\nsLocalSavePath=Other\\\n", encoding="utf-8"
        )
        load_env.return_value = qa_runner.SimpleNamespace(
            root=self.base_dir, profile="QaProfile", profile_dir=profile_dir
        )

        with self.assertRaisesRegex(qa_runner.ConfigError, "sLocalSavePath"):
            qa_runner.active_baseline_context()

    @patch("qa_runner.mo2ctl.read_selected_profile", return_value="OtherProfile")
    @patch("qa_runner.mo2ctl.load_env")
    def test_active_context_requires_selected_profile_match(self, load_env, _selected):
        profile_dir = self.base_dir / "profiles" / "QaProfile"
        load_env.return_value = qa_runner.SimpleNamespace(
            root=self.base_dir, profile="QaProfile", profile_dir=profile_dir
        )

        with self.assertRaisesRegex(qa_runner.ConfigError, "selected profile"):
            qa_runner.active_baseline_context()

    @patch("qa_runner.mo2ctl.read_selected_profile", return_value="QaProfile")
    @patch("qa_runner.mo2ctl.load_env")
    def test_malformed_profile_setting_becomes_failed_step_and_teardown_runs(
            self, load_env, _selected):
        profile_dir = self.base_dir / "profiles" / "QaProfile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "settings.ini").write_text(
            "[General]\nLocalSaves=maybe\nLocalSettings=true\n", encoding="utf-8"
        )
        (profile_dir / "skyrimcustom.ini").write_text(
            "[General]\nsLocalSavePath=__MO_Saves\\\n", encoding="utf-8"
        )
        load_env.return_value = qa_runner.SimpleNamespace(
            root=self.base_dir, profile="QaProfile", profile_dir=profile_dir
        )
        runner = qa_runner.Runner(
            {**self.spec,
             "steps": [{"type": "load_baseline"}],
             "teardown": [{"type": "wait", "seconds": 0}]},
            self.base_dir,
            interactive=False,
        )

        report = runner.run()

        self.assertEqual(report["status"], qa_runner.FAIL)
        self.assertIn("cannot read active profile settings", report["steps"][0]["error"])
        self.assertEqual(report["steps"][1]["status"], qa_runner.PASS)
        self.assertEqual(report["steps"][1]["phase"], "teardown")

    @patch("qa_runner.mo2ctl.read_selected_profile", side_effect=OSError("read failed"))
    @patch("qa_runner.mo2ctl.load_env")
    def test_selected_profile_io_error_becomes_config_error(self, load_env, _selected):
        load_env.return_value = qa_runner.SimpleNamespace(
            root=self.base_dir, profile="QaProfile", profile_dir=self.base_dir
        )

        with self.assertRaisesRegex(qa_runner.ConfigError, "cannot read MO2 selected"):
            qa_runner.active_baseline_context()

    @patch("qa_runner.bridge.state")
    @patch("qa_runner.bridge.console", return_value={"ok": True, "output": ""})
    def test_load_polls_until_full_state_fingerprint_matches(self, console, state):
        state.side_effect = [
            matching_state(**{"game.load_epoch": 7}),
            {"ok": False, "error": "game thread did not respond"},
            matching_state(**{"game.load_epoch": 8, "game.message_box.open": True}),
            matching_state(**{"game.load_epoch": 8}),
        ]

        result = self.runner.step_load_baseline({
            "type": "load_baseline",
            "retry_for": 10,
            "retry_interval": 0.5,
            "state_timeout": 3,
        })

        console.assert_called_once_with("load Baseline0A", timeout=60.0)
        self.assertEqual(state.call_count, 4)
        state.assert_called_with(timeout=3)
        self.assertEqual(self.fake_time.sleeps, [0.5, 0.5])
        self.assertTrue(result["state_fingerprint"]["matched"])
        self.assertEqual(result["state_fingerprint"]["attempts"], 3)
        self.assertEqual(
            result["state_fingerprint"]["load_epoch"], {"before": 7, "after": 8}
        )

    @patch("qa_runner.bridge.state")
    @patch("qa_runner.bridge.console", return_value={"ok": True})
    def test_open_message_box_never_counts_as_loaded(self, _console, state):
        state.side_effect = [
            matching_state(**{"game.load_epoch": 3}),
            matching_state(**{"game.load_epoch": 4, "game.message_box.open": True}),
            matching_state(**{"game.load_epoch": 4, "game.message_box.open": True}),
        ]

        with self.assertRaises(qa_runner.StepFailed) as caught:
            self.runner.step_load_baseline({
                "type": "load_baseline", "retry_for": 1, "retry_interval": 1,
            })

        self.assertIn("fingerprint did not match", str(caught.exception))
        self.assertEqual(caught.exception.failures[0]["path"], "game.message_box.open")
        self.assertEqual(self.fake_time.sleeps, [1])

    @patch("qa_runner.bridge.state")
    @patch("qa_runner.bridge.console", return_value={"ok": True})
    def test_matching_fingerprint_without_new_load_epoch_fails(self, _console, state):
        state.side_effect = [
            matching_state(**{"game.load_epoch": 9}),
            matching_state(**{"game.load_epoch": 9}),
            matching_state(**{"game.load_epoch": 9}),
        ]

        with self.assertRaises(qa_runner.StepFailed) as caught:
            self.runner.step_load_baseline({
                "type": "load_baseline", "retry_for": 1, "retry_interval": 1,
            })

        self.assertEqual(caught.exception.failures[0]["path"], "game.load_epoch")
        self.assertEqual(caught.exception.failures[0]["op"], "gt")

    @patch("qa_runner.bridge.state", return_value={
        "ok": True,
        "player": {},
        "game": {"message_box": {"open": False}},
    })
    @patch("qa_runner.bridge.console")
    def test_missing_load_epoch_fails_before_console(self, console, _state):
        with self.assertRaisesRegex(qa_runner.StepFailed, "game.load_epoch"):
            self.runner.step_load_baseline({"type": "load_baseline"})

        console.assert_not_called()

    def test_cell_form_id_hex_matches_runtime_decimal(self):
        failures, actual = qa_runner.check_state_signature(
            matching_state(),
            qa_runner.preflight_baseline(self.spec, self.base_dir)["state_signature"],
        )

        self.assertEqual(failures, [])
        self.assertEqual(actual["player.cell_form_id"], 0x0001A26F)

    def test_numeric_values_do_not_stand_in_for_boolean_fingerprint_fields(self):
        signature = qa_runner.preflight_baseline(
            self.spec, self.base_dir
        )["state_signature"]
        for path, numeric in (
            ("player.interior", 0),
            ("player.flags.dead", 0),
            ("game.message_box.open", 0),
        ):
            with self.subTest(path=path):
                failures, _actual = qa_runner.check_state_signature(
                    matching_state(**{path: numeric}), signature
                )
                self.assertEqual([failure["path"] for failure in failures], [path])
        for path in ("player.interior", "player.flags.dead"):
            with self.subTest(path=path, expected=True):
                true_signature = {**signature, path: True}
                failures, _actual = qa_runner.check_state_signature(
                    matching_state(**{path: 1}), true_signature
                )
                self.assertEqual([failure["path"] for failure in failures], [path])

    def test_legacy_string_baseline_has_clear_migration_error(self):
        spec = {"baseline": "Baseline0A", "steps": [{"type": "load_baseline"}]}

        problems = qa_runner.validate(spec, self.base_dir)

        self.assertEqual(len(problems), 1)
        self.assertIn("save-name string is no longer sufficient", problems[0])

    @patch("qa_runner.bridge.console")
    def test_legacy_string_cannot_bypass_validation(self, console):
        runner = qa_runner.Runner(
            {"baseline": "Baseline0A"},
            self.base_dir,
            interactive=False,
            baseline_context=lambda: {
                "profile": "QaProfile", "save_directory": self.base_dir,
            },
        )

        with self.assertRaisesRegex(qa_runner.StepFailed, "save-name string"):
            runner.step_load_baseline({"type": "load_baseline"})

        console.assert_not_called()

    def test_manifest_requires_closed_pair_and_safe_state_signature(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["pair"]["members"] = manifest["pair"]["members"][:1]
        manifest["state_signature"]["game.message_box.open"] = True
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

        problems = qa_runner.validate(
            {**self.spec, "steps": [{"type": "load_baseline"}]}, self.base_dir
        )

        self.assertIn("exactly .ess and .skse", problems[0])

    def test_manifest_requires_closed_message_box_signature(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["state_signature"]["game.message_box.open"] = True
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

        problems = qa_runner.validate(
            {**self.spec, "steps": [{"type": "load_baseline"}]}, self.base_dir
        )

        self.assertIn("game.message_box.open` must be false", problems[0])

    def test_manifest_rejects_multivalue_fingerprint_paths(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["state_signature"]["plugins[*].name"] = "Example.esp"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

        problems = qa_runner.validate(
            {**self.spec, "steps": [{"type": "load_baseline"}]}, self.base_dir
        )

        self.assertIn("wildcards are not allowed", problems[0])


class SemanticStepTests(unittest.TestCase):
    def setUp(self):
        self.runner = qa_runner.Runner({"defaults": {}}, Path.cwd(), interactive=False)

    @patch("qa_runner.wait_for", return_value={"ok": True})
    @patch("qa_runner._mo2")
    def test_launch_keeps_game_thread_active_in_background_by_default(self, call_mo2, _wait):
        call_mo2.return_value = {
            "bridge": {"reachable": True},
            "launched": True,
        }

        result = self.runner.step_launch({"type": "launch", "wait": 30})

        self.assertTrue(result["launched"])
        call_mo2.assert_called_once_with(
            qa_runner.mo2ctl.cmd_launch,
            shortcut="SKSE",
            wait=30,
            no_wait=False,
            background_active=True,
        )

    @patch("qa_runner.bridge.move_to_actor")
    def test_move_to_actor_uses_name_and_distance(self, move):
        move.return_value = {"ok": True, "actor": {"name": "Falas"}}
        result = self.runner.step_move_to_actor(
            {"type": "move_to_actor", "name": "Falas", "distance": 96})
        self.assertTrue(result["ok"])
        move.assert_called_once_with(
            "Falas", form_id=None, scope="cell", distance=96, timeout=20.0)

    @patch("qa_runner.bridge.move_to_actor")
    def test_move_to_actor_accepts_form_id_and_retries(self, move):
        move.side_effect = [
            {"ok": False, "error": "not loaded"},
            {"ok": True, "actor": {"form_id": 0x1234}},
        ]
        result = self.runner.step_move_to_actor({
            "type": "move_to_actor", "form_id": "0x1234", "scope": "loaded",
            "retry_for": 1, "retry_interval": 0,
        })
        self.assertEqual(result["attempts"], 2)
        move.assert_called_with(
            None, form_id="0x1234", scope="loaded", distance=128.0, timeout=20.0)

    @patch("qa_runner.bridge.select_dialogue")
    def test_select_dialogue_reports_available_options(self, select):
        select.return_value = {
            "ok": False,
            "error": "dialogue option not found",
            "available": ["Fund", "Parley"],
        }
        with self.assertRaisesRegex(qa_runner.StepFailed, "available=.*Parley"):
            self.runner.step_select_dialogue(
                {"type": "select_dialogue", "text": "Missing"})

    @patch("qa_runner.bridge.select_dialogue")
    def test_select_dialogue_accepts_topic_info_form_id(self, select):
        select.return_value = {"ok": True, "info_form_id": 0xABC}
        result = self.runner.step_select_dialogue({
            "type": "select_dialogue", "info_form_id": "0xABC",
        })
        self.assertEqual(result["info_form_id"], 0xABC)
        select.assert_called_once_with(
            None, contains=False, index=None, info_form_id="0xABC", timeout=20.0)

    @patch("qa_runner.bridge.select_message_box")
    def test_select_message_box_retries_with_message_guard(self, select):
        select.side_effect = [
            {"ok": False, "error": "MessageBoxMenu is not open"},
            {"ok": True, "message": "Done Writing", "text": "OK", "index": 0},
        ]
        result = self.runner.step_select_message_box({
            "type": "select_message_box", "text": "OK", "message": "Done Writing",
            "retry_for": 1, "retry_interval": 0,
        })
        self.assertEqual(result["attempts"], 2)
        select.assert_called_with(
            "OK", index=None, message="Done Writing", timeout=20.0)

    @patch("qa_runner.bridge.global_value")
    def test_assert_global_uses_structured_value(self, global_value):
        global_value.return_value = {"ok": True, "editor_id": "Favor", "value": 5.0}
        result = self.runner.step_assert_global({
            "type": "assert_global",
            "editor_id": "Favor",
            "expect": {"eq": 5},
            "retry_for": 0,
        })
        self.assertEqual(result["value"], 5.0)

    def test_semantic_steps_validate(self):
        spec = {
            "steps": [
                {"type": "move_to_actor", "name": "Falas"},
                {"type": "activate_actor", "form_id": "0x1234", "scope": "loaded"},
                {"type": "select_dialogue", "info_form_id": "0x5678"},
                {"type": "assert_global", "editor_id": "Favor", "expect": {"eq": 5}},
                {"type": "close_dialogue"},
                {"type": "select_message_box", "text": "OK", "message": "Done Writing"},
            ]
        }
        self.assertEqual(qa_runner.validate(spec, Path(tempfile.gettempdir())), [])

    def test_semantic_steps_reject_missing_selectors(self):
        spec = {
            "steps": [
                {"type": "move_to_actor"},
                {"type": "select_dialogue"},
                {"type": "select_message_box"},
                {"type": "select_message_box", "index": -1},
                {"type": "assert_global", "expect": {"wat": 1}},
            ]
        }
        problems = qa_runner.validate(spec, Path(tempfile.gettempdir()))
        self.assertTrue(any("move_to_actor needs exactly one" in p for p in problems))
        self.assertTrue(any("select_dialogue needs exactly one" in p for p in problems))
        self.assertTrue(any("select_message_box needs exactly one" in p for p in problems))
        self.assertTrue(any("select_message_box `index` must be a non-negative" in p
                            for p in problems))
        self.assertTrue(any("assert_global needs `editor_id`" in p for p in problems))
        self.assertTrue(any("unknown operator 'wat'" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
