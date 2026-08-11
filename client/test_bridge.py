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


if __name__ == "__main__":
    unittest.main()
