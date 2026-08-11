#!/usr/bin/env python3

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_runner


class SemanticStepTests(unittest.TestCase):
    def setUp(self):
        self.runner = qa_runner.Runner({"defaults": {}}, Path.cwd(), interactive=False)

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
