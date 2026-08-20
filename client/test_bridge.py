#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge


class SemanticRequestTests(unittest.TestCase):
    @patch("bridge._request")
    def test_actor_form_id_request(self, request):
        request.return_value = {"ok": True}
        bridge.move_to_actor(form_id="0x1234", scope="loaded", distance=96)
        request.assert_called_once_with(
            "POST", "/actor/move-to",
            body={"scope": "loaded", "form_id": "0x1234", "distance": 96},
            timeout=bridge.DEFAULT_TIMEOUT)

    @patch("bridge._request")
    def test_dialogue_index_request(self, request):
        request.return_value = {"ok": True}
        bridge.select_dialogue(index=2)
        request.assert_called_once_with(
            "POST", "/dialogue/select",
            body={"contains": False, "index": 2}, timeout=bridge.DEFAULT_TIMEOUT)

    @patch("bridge._request")
    def test_message_box_request_has_exact_message_guard(self, request):
        request.return_value = {"ok": True}
        bridge.select_message_box("OK", message="Done Writing")
        request.assert_called_once_with(
            "POST", "/messagebox/select",
            body={"text": "OK", "message": "Done Writing"},
            timeout=bridge.DEFAULT_TIMEOUT)


class ActorValueReadTests(unittest.TestCase):
    @patch("bridge.time.sleep")
    @patch("bridge.console")
    def test_requires_repeated_exact_getav_lines(self, console, _sleep):
        console.side_effect = [
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 15.00\n"]},
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 15.00\n"]},
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 15.00\n"]},
        ]

        result = bridge.actor_value("HeavyArmor", "0x14")

        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 15.0)
        self.assertEqual(result["consecutive"], 3)
        self.assertEqual(console.call_count, 3)

    @patch("bridge.time.sleep")
    @patch("bridge.console")
    def test_rejects_foreign_or_wrongly_shaped_output(self, console, _sleep):
        console.side_effect = [
            {"ok": True, "output": ["GetInFaction >> 0.00\n"]},
            {"ok": True, "output": ["GetActorValue: Health >> 100.00\n"]},
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 20.00\n"]},
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 20.00\n"]},
        ]

        result = bridge.actor_value(
            "HeavyArmor", "0x000A2C94", consecutive=2, max_attempts=4)

        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 20.0)
        self.assertEqual(len(result["rejected"]), 2)

    @patch("bridge.time.sleep")
    @patch("bridge.console")
    def test_changed_value_restarts_streak(self, console, _sleep):
        console.side_effect = [
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 15.00\n"]},
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 40.00\n"]},
            {"ok": True, "output": ["GetActorValue: HeavyArmor >> 40.00\n"]},
        ]

        result = bridge.actor_value(
            "HeavyArmor", "0x14", consecutive=2, max_attempts=3)

        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 40.0)
        self.assertEqual(result["consecutive"], 2)

    def test_rejects_invalid_arguments_without_console(self):
        self.assertFalse(bridge.actor_value("Heavy Armor", "0x14")["ok"])
        self.assertFalse(bridge.actor_value("HeavyArmor", "", consecutive=3)["ok"])
        self.assertFalse(bridge.actor_value("HeavyArmor", "0x14", consecutive=1)["ok"])


if __name__ == "__main__":
    unittest.main()
