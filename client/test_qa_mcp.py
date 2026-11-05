#!/usr/bin/env python3

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_mcp


class SemanticToolTests(unittest.TestCase):
    @patch("qa_mcp.bridge.input_tap")
    def test_input_tap_routes_once_and_preserves_dispatch_result(self, tap):
        expected = {"ok": True, "schema": 1, "request_id": 17, "state": "completed",
                    "down_dispatched": True, "up_dispatched": True, "effect": "unknown"}
        tap.return_value = expected
        result = qa_mcp.tool_qa_input_tap({"scan_code": 18, "timeout": 2.5})
        self.assertEqual(result, expected)
        tap.assert_called_once_with(18, timeout=2.5)

    @patch("qa_mcp.bridge.input_tap")
    def test_input_tap_defaults_timeout(self, tap):
        tap.return_value = {"ok": True}
        qa_mcp.tool_qa_input_tap({"scan_code": 254})
        tap.assert_called_once_with(254, timeout=3.0)

    @patch("qa_mcp.bridge.input_tap")
    def test_input_tap_rejects_invalid_arguments_without_sending(self, tap):
        cases = [{"scan_code": scan} for scan in (None, True, False, 0, -1, 255, 256, 18.0, "18")]
        cases += [{"scan_code": 18, "timeout": timeout}
                  for timeout in (None, True, False, 0, -1, float("nan"), float("inf"), "3", 10**1000)]
        cases += [{}, {"scan_code": 18, "retry_for": 10}, {"scan_code": 18, "action": "tap"}, []]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(qa_mcp.ToolError):
                qa_mcp.tool_qa_input_tap(args)
        tap.assert_not_called()

    @patch("qa_mcp.bridge.input_tap")
    def test_input_tap_unknown_error_retains_full_json_without_retry(self, tap):
        expected = {"ok": False, "schema": 1, "request_id": 19, "state": "unknown",
                    "reason": "deadline", "down_dispatched": True, "up_dispatched": False,
                    "effect": "unknown"}
        tap.return_value = expected
        with patch("qa_mcp.qa_runner.retry_for_ok") as retry:
            result = qa_mcp.handle_tools_call({"name": "qa_input_tap", "arguments": {"scan_code": 18}})
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), expected)
        tap.assert_called_once_with(18, timeout=3.0)
        retry.assert_not_called()

    def test_input_tap_schema_matches_strict_dispatch_contract(self):
        tool = next(tool for tool in qa_mcp.TOOLS if tool["name"] == "qa_input_tap")
        schema = tool["inputSchema"]
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["scan_code"])
        self.assertEqual(schema["properties"]["scan_code"], {"type": "integer", "minimum": 1, "maximum": 254})
        self.assertIn("completed", tool["description"])
        self.assertIn("unknown", tool["description"])

    @patch("qa_mcp.bridge.select_message_box")
    def test_message_box_routes_index_and_guard(self, select):
        select.return_value = {"ok": True}
        qa_mcp.tool_qa_message_box({
            "index": 0, "message": "Done Writing", "retry_for": 1,
        })
        select.assert_called_once_with(None, index=0, message="Done Writing")

    def test_message_box_rejects_multiple_selectors(self):
        with self.assertRaisesRegex(qa_mcp.ToolError, "exactly one"):
            qa_mcp.tool_qa_message_box({"text": "OK", "index": 0})

    def test_message_box_rejects_negative_index(self):
        with self.assertRaisesRegex(qa_mcp.ToolError, "non-negative"):
            qa_mcp.tool_qa_message_box({"index": -1})

    def test_semantic_tools_are_published(self):
        names = {tool["name"] for tool in qa_mcp.TOOLS}
        self.assertTrue({"qa_actor", "qa_dialogue", "qa_message_box", "qa_global", "qa_wait"} <= names)

    @patch("qa_mcp.bridge.state")
    def test_wait_polls_until_structured_state_matches(self, state):
        state.side_effect = [
            {"ok": True, "player": {"cell_form_id": 1}},
            {"ok": True, "player": {"cell_form_id": 2}},
        ]
        result = qa_mcp.tool_qa_wait({
            "expect": {"player.cell_form_id": {"eq": 2}},
            "retry_for": 1,
            "retry_interval": 0,
        })
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["state"]["player"]["cell_form_id"], 2)

    @patch("qa_mcp.bridge.state")
    def test_wait_can_match_exact_message_box(self, state):
        state.side_effect = [
            {"ok": True, "game": {"message_box": {"open": False}}},
            {"ok": True, "game": {"message_box": {
                "open": True, "ready": True, "message": "Done Writing",
                "buttons": [{"index": 0, "text": "OK"}],
            }}},
        ]
        result = qa_mcp.tool_qa_wait({
            "expect": {
                "game.message_box.message": {"eq": "Done Writing"},
                "game.message_box.buttons[*].text": {"eq": "OK"},
            },
            "retry_for": 1,
            "retry_interval": 0,
        })
        self.assertEqual(result["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
