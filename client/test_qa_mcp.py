#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_mcp


class SemanticToolTests(unittest.TestCase):
    @patch("qa_mcp.bridge.state")
    def test_state_accepts_cell_actor_block(self, state):
        state.return_value = {"ok": True, "cell_actors": [{"name": "Falas"}]}
        result = qa_mcp.tool_qa_state({"include": ["cell_actors"]})
        self.assertEqual(result["cell_actors"][0]["name"], "Falas")
        state.assert_called_once_with(["cell_actors"], radius=None, limit=None)

    @patch("qa_mcp.bridge.state")
    def test_state_accepts_loaded_actor_block(self, state):
        state.return_value = {"ok": True, "loaded_actors": [{"name": "Falas"}]}
        result = qa_mcp.tool_qa_state({"include": ["loaded_actors"]})
        self.assertEqual(result["loaded_actors"][0]["name"], "Falas")

    @patch("qa_mcp.bridge.move_to_actor")
    def test_actor_move_routes_name_and_distance(self, move):
        move.return_value = {"ok": True}
        qa_mcp.tool_qa_actor({"action": "move_to", "name": "Falas", "distance": 96})
        move.assert_called_once_with(
            "Falas", form_id=None, scope="cell", distance=96)

    @patch("qa_mcp.bridge.move_to_actor")
    def test_actor_move_routes_form_id_and_loaded_scope(self, move):
        move.return_value = {"ok": True}
        qa_mcp.tool_qa_actor({
            "action": "move_to", "form_id": "0x1234", "scope": "loaded",
        })
        move.assert_called_once_with(
            None, form_id="0x1234", scope="loaded", distance=128.0)

    @patch("qa_mcp.bridge.activate_actor")
    def test_actor_activate_routes_name(self, activate):
        activate.return_value = {"ok": True}
        qa_mcp.tool_qa_actor({"action": "activate", "name": "Falas"})
        activate.assert_called_once_with("Falas", form_id=None, scope="cell")

    @patch("qa_mcp.bridge.select_dialogue")
    def test_dialogue_select_routes_text(self, select):
        select.return_value = {"ok": True}
        qa_mcp.tool_qa_dialogue({"action": "select", "text": "Let's talk.", "contains": True})
        select.assert_called_once_with(
            "Let's talk.", contains=True, index=None, info_form_id=None)

    @patch("qa_mcp.bridge.select_dialogue")
    def test_dialogue_select_routes_index(self, select):
        select.return_value = {"ok": True}
        qa_mcp.tool_qa_dialogue({"action": "select", "index": 2})
        select.assert_called_once_with(None, contains=False, index=2, info_form_id=None)

    @patch("qa_mcp.bridge.close_dialogue")
    def test_dialogue_close(self, close):
        close.return_value = {"ok": True}
        qa_mcp.tool_qa_dialogue({"action": "close"})
        close.assert_called_once_with()

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

    @patch("qa_mcp.bridge.global_value")
    def test_global_read(self, global_value):
        global_value.return_value = {"ok": True, "value": 5.0}
        result = qa_mcp.tool_qa_global({"editor_id": "Favor"})
        self.assertEqual(result["value"], 5.0)

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
