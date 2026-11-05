#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_mcp


class SemanticToolTests(unittest.TestCase):
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
