#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mo2ctl


class Mo2CtlLaunchDelegationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mo2"
        self.profile = self.root / "profiles" / "QA"
        self.profile.mkdir(parents=True)
        (self.root / "ModOrganizer.ini").write_bytes(
            b"[General]\r\nselected_profile=@ByteArray(QA)\r\n"
        )
        self.env = mo2ctl.Env(self.root, "QA")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def args(
        self, *, background_active: bool = False, no_wait: bool = True,
        wait: float = 1,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            shortcut="SKSE", wait=wait, no_wait=no_wait,
            background_active=background_active,
        )

    def fake_script(self) -> Path:
        script = Path(self.tmp.name) / "launch-mo2.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        return script

    def test_env_override_delegates_exact_argv_and_preserves_no_wait_result(self) -> None:
        script = self.fake_script()
        log_path = Path(self.tmp.name) / "mo2ctl-launch.log"
        with (
            patch.dict(os.environ, {
                "MO2CTL_LAUNCH_SCRIPT": str(script),
                "MO2CTL_LOG_DIR": self.tmp.name,
            }),
            patch("mo2ctl.game_pids", return_value=[]),
            patch("mo2ctl.subprocess.Popen") as popen,
        ):
            popen.return_value.pid = 4242
            result = mo2ctl.cmd_launch(self.env, self.args())

        self.assertEqual(popen.call_args.args[0], [str(script), "--shortcut", "SKSE"])
        self.assertEqual(result, {
            "launched": True,
            "pid": 4242,
            "shortcut": "SKSE",
            "log": str(log_path),
        })

    def test_empty_override_uses_repository_launch_script(self) -> None:
        with patch.dict(os.environ, {"MO2CTL_LAUNCH_SCRIPT": ""}):
            script = mo2ctl.launch_script_path()

        self.assertEqual(
            script,
            Path(mo2ctl.__file__).resolve().parents[3]
            / "instance" / "tools" / "launch-mo2.sh",
        )
        self.assertTrue(str(script).endswith("instance/tools/launch-mo2.sh"))

    def test_missing_script_raises_fail_with_both_recovery_paths(self) -> None:
        missing = Path(self.tmp.name) / "missing-launch-mo2.sh"
        with (
            patch.dict(os.environ, {"MO2CTL_LAUNCH_SCRIPT": str(missing)}),
            patch("mo2ctl.game_pids", return_value=[]),
            patch("mo2ctl.subprocess.Popen") as popen,
            self.assertRaises(mo2ctl.Fail) as raised,
        ):
            mo2ctl.cmd_launch(self.env, self.args())

        message = str(raised.exception)
        self.assertIn("MO2CTL_LAUNCH_SCRIPT", message)
        self.assertIn("instance/tools/launch-mo2.sh --skse", message)
        popen.assert_not_called()

    def test_spawn_failure_restores_background_active_original_bytes(self) -> None:
        script = self.fake_script()
        original = b"[General]\r\nbAlwaysActive=0\r\nsLanguage=ENGLISH\r\n"
        (self.profile / "skyrim.ini").write_bytes(original)
        with (
            patch.dict(os.environ, {
                "MO2CTL_LAUNCH_SCRIPT": str(script),
                "MO2CTL_LOG_DIR": self.tmp.name,
            }),
            patch("mo2ctl.game_pids", return_value=[]),
            patch("mo2ctl.mo2_pids", return_value=[]),
            patch("mo2ctl.subprocess.Popen", side_effect=OSError("spawn failed")),
            self.assertRaisesRegex(OSError, "spawn failed"),
        ):
            mo2ctl.cmd_launch(self.env, self.args(background_active=True))

        self.assertEqual((self.profile / "skyrim.ini").read_bytes(), original)
        self.assertFalse(mo2ctl.background_active_backup(self.env).exists())

    def test_wait_returns_when_bridge_becomes_reachable(self) -> None:
        script = self.fake_script()
        bridge = {"reachable": True, "ok": True, "version": "test"}
        with (
            patch.dict(os.environ, {
                "MO2CTL_LAUNCH_SCRIPT": str(script),
                "MO2CTL_LOG_DIR": self.tmp.name,
            }),
            patch("mo2ctl.game_pids", return_value=[]),
            patch("mo2ctl.subprocess.Popen") as popen,
            patch("mo2ctl.bridge_status", return_value=bridge),
            patch("mo2ctl.time.monotonic", side_effect=[100, 100, 101]),
        ):
            popen.return_value.pid = 4242
            result = mo2ctl.cmd_launch(
                self.env, self.args(no_wait=False, wait=10),
            )

        self.assertEqual(result["bridge"], bridge)
        self.assertEqual(result["waited_seconds"], 1)

    def test_wait_reports_launcher_exit_before_bridge(self) -> None:
        script = self.fake_script()
        with (
            patch.dict(os.environ, {
                "MO2CTL_LAUNCH_SCRIPT": str(script),
                "MO2CTL_LOG_DIR": self.tmp.name,
            }),
            patch("mo2ctl.game_pids", return_value=[]),
            patch("mo2ctl.subprocess.Popen") as popen,
            patch("mo2ctl.bridge_status", return_value={"reachable": False}),
            patch("mo2ctl.time.monotonic", side_effect=[100, 100]),
        ):
            popen.return_value.pid = 4242
            popen.return_value.poll.return_value = 7
            popen.return_value.returncode = 7
            result = mo2ctl.cmd_launch(
                self.env, self.args(no_wait=False, wait=10),
            )

        self.assertEqual(result["bridge"], {
            "reachable": False,
            "error": "launcher exited with 7 before the bridge came up",
        })

    def test_wait_reports_timeout(self) -> None:
        script = self.fake_script()
        with (
            patch.dict(os.environ, {
                "MO2CTL_LAUNCH_SCRIPT": str(script),
                "MO2CTL_LOG_DIR": self.tmp.name,
            }),
            patch("mo2ctl.game_pids", return_value=[]),
            patch("mo2ctl.subprocess.Popen") as popen,
            patch("mo2ctl.bridge_status") as bridge_status,
            patch("mo2ctl.time.monotonic", side_effect=[100, 102]),
        ):
            popen.return_value.pid = 4242
            result = mo2ctl.cmd_launch(
                self.env, self.args(no_wait=False, wait=1),
            )

        self.assertEqual(result["bridge"], {
            "reachable": False,
            "error": "no /ping within 1s",
        })
        bridge_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
