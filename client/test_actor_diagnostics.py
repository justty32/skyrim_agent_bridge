"""Synthetic contract tests; no game, private actor data or console calls."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge
from actor_diagnostics import parse_actor_diagnostics

FIXTURE = Path(__file__).parent / "fixtures" / "actor-diagnostics-v1.json"
REFERENCE = "0x01001234"


def snapshot():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def hello(versions=None):
    return {"ok": True, "plugin": "AgentBridge", "version": "future",
            "capabilities": {"actor_diagnostics": [1] if versions is None else versions}}


class ParserTests(unittest.TestCase):
    def parse(self, payload):
        return parse_actor_diagnostics(payload, form_id=REFERENCE)

    def test_preserves_independent_hands_and_spell_slots(self):
        payload = snapshot()
        payload["future_metadata"] = {"supported": True}
        result = self.parse(payload)
        self.assertEqual(result, payload)
        self.assertIsNot(result, payload)
        self.assertEqual(result["equipped"]["left"]["status"], "empty")
        self.assertEqual(result["selected_spells"]["left"]["status"], "value")
        self.assertEqual(result["combat_style"]["reference_override"]["status"], "unavailable")

    def test_all_empty_and_all_unavailable_are_valid_snapshots(self):
        for reading in ({"status": "empty", "value": None},
                        {"status": "unavailable", "reason": "actor_process_unavailable"}):
            payload = snapshot()
            for group in ("equipped", "selected_spells", "combat_style"):
                for key in payload[group]:
                    payload[group][key] = copy.deepcopy(reading)
            with self.subTest(reading=reading):
                self.assertTrue(self.parse(payload)["ok"])

    def test_rejects_contradictory_or_missing_readings(self):
        for reading in (None, {}, {"status": "empty"}, {"status": "empty", "value": 0},
                        {"status": "empty", "value": {}}, {"status": "value", "value": None},
                        {"status": "unavailable"}, {"status": "unavailable", "reason": " "},
                        {"status": "unavailable", "reason": "not_loaded", "value": None},
                        {"status": "empty", "value": None, "reason": "not_loaded"},
                        {"status": "unknown"}):
            payload = snapshot()
            payload["equipped"]["right"] = reading
            with self.subTest(reading=reading):
                self.assertEqual(self.parse(payload)["status"], "invalid_response")

    def test_rejects_malformed_envelope_and_missing_required_fields(self):
        payload = snapshot()
        cases = [None, [], "text", 1, {}, {**payload, "ok": 1},
                 {**payload, "schema_version": True}, {**payload, "schema_version": 2}]
        for key in ("actor", "equipped", "selected_spells", "combat_style"):
            cases.append({k: v for k, v in payload.items() if k != key})
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(self.parse(value)["status"], "invalid_response")

    def test_rejects_wrong_reference_and_invalid_numeric_ids(self):
        for key in ("form_id", "base_form_id"):
            for value in (0, True, -1, 0x100000000, "0x01001234", 1.0):
                payload = snapshot()
                payload["actor"][key] = value
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.parse(payload)["status"], "invalid_response")
        payload = snapshot()
        payload["actor"]["form_id"] += 1
        self.assertIn("does not match", self.parse(payload)["error"])

    def test_form_metadata_and_type_are_checked(self):
        for key, value in (("form_id", 0), ("form_id", False), ("name", None),
                           ("editor_id", None), ("form_type", ""), ("form_type", "weapon")):
            payload = snapshot()
            payload["equipped"]["right"]["value"][key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.parse(payload)["status"], "invalid_response")
        payload = snapshot()
        payload["combat_style"]["base"]["value"]["form_type"] = "WEAP"
        self.assertEqual(self.parse(payload)["status"], "invalid_response")

    def test_query_failure_and_unsupported_are_never_empty(self):
        for status in ("query_failed", "unsupported"):
            result = self.parse({"ok": False, "status": status, "error": "synthetic failure"})
            self.assertEqual(result["status"], status)
            self.assertNotIn("equipped", result)
        self.assertEqual(self.parse({"ok": False, "error": "game thread timeout"})["status"],
                         "query_failed")
        self.assertEqual(self.parse({"ok": False})["status"], "invalid_response")


class QueryTests(unittest.TestCase):
    @patch("bridge._request")
    def test_legacy_dll_is_unsupported_without_requesting_new_route(self, request):
        request.return_value = {"ok": True, "plugin": "AgentBridge", "version": "0.9.0"}
        self.assertEqual(bridge.actor_diagnostics(form_id=REFERENCE)["status"], "unsupported")
        request.assert_called_once_with("GET", "/ping", timeout=bridge.DEFAULT_TIMEOUT)

    @patch("bridge._request")
    def test_capability_gate(self, request):
        for caps, status in (({}, "unsupported"), ({"actor_diagnostics": [2]}, "unsupported"),
                             ({"actor_diagnostics": []}, "unsupported"), (None, "invalid_response"),
                             ({"actor_diagnostics": 1}, "invalid_response"),
                             ({"actor_diagnostics": [True]}, "invalid_response"),
                             ({"actor_diagnostics": [0]}, "invalid_response")):
            request.reset_mock()
            request.return_value = {**hello(), "capabilities": caps}
            with self.subTest(caps=caps):
                self.assertEqual(bridge.actor_diagnostics(form_id=REFERENCE)["status"], status)
                self.assertEqual(request.call_count, 1)

    @patch("bridge._request")
    def test_negotiated_get_and_hex_integer_identity(self, request):
        for reference in (REFERENCE, "01001234", 0x01001234):
            request.reset_mock()
            request.side_effect = [hello([2, 1]), snapshot()]
            result = bridge.actor_diagnostics(form_id=reference, scope="loaded", timeout=2.5)
            self.assertTrue(result["ok"])
            self.assertEqual(request.call_args.args,
                             ("GET", "/actor/diagnostics?form_id=0x01001234&scope=loaded&schema_version=1"))
            self.assertEqual(request.call_args.kwargs, {"timeout": 2.5})

    @patch("bridge._request")
    def test_invalid_request_never_calls_network(self, request):
        for reference in (0, True, -1, "0x", " 0x1234", "1;kill", 0x100000000, "0x14"):
            with self.subTest(reference=reference):
                self.assertEqual(bridge.actor_diagnostics(form_id=reference)["status"], "invalid_request")
        self.assertEqual(bridge.actor_diagnostics(form_id=REFERENCE, scope="all")["status"],
                         "invalid_request")
        request.assert_not_called()

    @patch("bridge._request")
    def test_failed_or_malformed_ping_stops_before_get(self, request):
        for payload, status in (({"ok": False, "error": "connection refused"}, "query_failed"),
                                 ([], "invalid_response"), ({"ok": 1}, "invalid_response")):
            request.reset_mock()
            request.return_value = payload
            with self.subTest(payload=payload):
                self.assertEqual(bridge.actor_diagnostics(form_id=REFERENCE)["status"], status)
                self.assertEqual(request.call_count, 1)

    @patch("bridge._request")
    def test_advertised_route_errors_never_become_empty(self, request):
        for payload, status in (({"ok": False, "error": "HTTP 404"}, "query_failed"),
                                 ({"ok": False, "error": "game thread did not respond in time"}, "query_failed"),
                                 ({"ok": False, "status": "unsupported", "error": "schema unsupported"}, "unsupported"),
                                 ([], "invalid_response"), ({"ok": True}, "invalid_response")):
            request.side_effect = [hello(), payload]
            with self.subTest(payload=payload):
                self.assertEqual(bridge.actor_diagnostics(form_id=REFERENCE)["status"], status)
        request.side_effect = TimeoutError("synthetic timeout")
        self.assertEqual(bridge.actor_diagnostics(form_id=REFERENCE)["status"], "query_failed")


class OfflineCLITests(unittest.TestCase):
    def test_cli_accepts_valid_snapshot_and_rejects_identity_mismatch(self):
        script = str(Path(__file__).parent / "actor_diagnostics.py")
        for reference, expected in ((REFERENCE, 0), ("0x01009999", 1)):
            result = subprocess.run([sys.executable, script, "--ref", reference,
                                     "--snapshot", str(FIXTURE)], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(json.loads(result.stdout)["ok"], expected == 0)


if __name__ == "__main__":
    unittest.main()
