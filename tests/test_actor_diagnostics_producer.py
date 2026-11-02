"""Execute the unchanged production function against host RE doubles, never Skyrim.

Requires an existing host compiler and nlohmann headers. No download/install.
The Windows build separately checks the real CommonLib ABI and route compilation.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
from actor_diagnostics import parse_actor_diagnostics


class ProducerTests(unittest.TestCase):
    def test_real_producer_success_failures_and_contract(self):
        source = (ROOT / "src/GameActions.cpp").read_text()
        start = source.index("json GameActions::ActorDiagnostics(")
        end = source.index("json GameActions::MoveToActor(", start)
        harness = (ROOT / "tests/actor_diagnostics_harness.cpp").read_text()
        harness = harness.replace("// PRODUCER_BODY", source[start:end])
        routes = (ROOT / "src/Routes.cpp").read_text()
        parser_start = routes.index("    std::optional<RE::FormID> ParseFormID(")
        parser_end = routes.index("    RE::FormID BodyFormID(", parser_start)
        harness = harness.replace("// PARSER_BODY", routes[parser_start:parser_end])
        start = routes.index("    Http::Response ActorDiagnostics(")
        end = routes.index("    Http::Response ActorMove(", start)
        harness = harness.replace("// ROUTE_BODY", routes[start:end])
        include = ROOT / "build/release-clang-cl-linux/vcpkg_installed/x64-windows-skse-clang/include"
        self.assertTrue((include / "nlohmann/json.hpp").is_file(), "existing nlohmann headers required")
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "test.cpp"
            executable = Path(directory) / "producer"
            cpp.write_text(harness)
            subprocess.run(["nice", "-n", "19", "g++", "-std=c++23", "-O0", "-I", str(include),
                            str(cpp), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
        cases = json.loads(result.stdout)
        for payload in cases[:3]:
            self.assertTrue(parse_actor_diagnostics(payload, form_id=0x01001234)["ok"])
        self.assertEqual(cases[0]["equipped"]["right"]["status"], "unavailable")
        self.assertEqual(cases[1]["equipped"]["right"]["status"], "empty")
        self.assertEqual(cases[2]["equipped"]["right"]["value"]["form_type"], "WEAP")
        self.assertEqual(cases[2]["equipped"]["left"]["status"], "empty")
        self.assertEqual(cases[2]["selected_spells"]["left"]["value"]["form_type"], "SPEL")
        self.assertEqual(cases[2]["selected_spells"]["right"]["status"], "empty")
        for field in ("base", "reference_override"):
            self.assertEqual(cases[2]["combat_style"][field]["value"]["form_type"], "CSTY")
        for payload in cases[3:]:
            self.assertEqual(parse_actor_diagnostics(payload, form_id=0x01001234)["status"], "query_failed")
            self.assertNotIn("equipped", payload)
        del cases[2]["selected_spells"]["left"]
        self.assertEqual(parse_actor_diagnostics(cases[2], form_id=0x01001234)["status"], "invalid_response")


if __name__ == "__main__":
    unittest.main()
