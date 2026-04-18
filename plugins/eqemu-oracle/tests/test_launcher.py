from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class LauncherTest(unittest.TestCase):
    def test_launcher_help_works_on_current_platform(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "scripts" / "eqemu_oracle_launcher.cmd"
        completed = subprocess.run(
            [str(launcher), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("EQEmu Oracle plugin runtime", completed.stdout)
        if sys.platform != "win32":
            self.assertEqual(completed.stderr, "")
