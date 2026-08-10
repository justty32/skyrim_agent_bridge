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
        move.assert_called_once_with("Falas", distance=96, timeout=20.0)

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
                {"type": "activate_actor", "name": "Falas"},
                {"type": "select_dialogue", "text": "Let's talk."},
                {"type": "assert_global", "editor_id": "Favor", "expect": {"eq": 5}},
                {"type": "close_dialogue"},
            ]
        }
        self.assertEqual(qa_runner.validate(spec, Path(tempfile.gettempdir())), [])

    def test_semantic_steps_reject_missing_selectors(self):
        spec = {
            "steps": [
                {"type": "move_to_actor"},
                {"type": "select_dialogue"},
                {"type": "assert_global", "expect": {"wat": 1}},
            ]
        }
        problems = qa_runner.validate(spec, Path(tempfile.gettempdir()))
        self.assertTrue(any("move_to_actor needs `name`" in p for p in problems))
        self.assertTrue(any("select_dialogue needs `text`" in p for p in problems))
        self.assertTrue(any("assert_global needs `editor_id`" in p for p in problems))
        self.assertTrue(any("unknown operator 'wat'" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
