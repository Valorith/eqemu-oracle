from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import cli  # noqa: E402


class CliRefreshTest(unittest.TestCase):
    def test_scoped_committed_refresh_only_clears_target_domain(self) -> None:
        args = SimpleNamespace(mode="committed", scope="schema")
        with patch("eqemu_oracle.cli.refresh_dataset", return_value={"extension_health": {}}) as refresh_dataset:
            exit_code = cli.refresh(args)

        self.assertEqual(exit_code, 0)
        refresh_dataset.assert_called_once_with(scope="schema", mode="committed")

    def test_update_plugin_forwards_restore_branch(self) -> None:
        args = SimpleNamespace(
            remote="origin",
            branch="release",
            allow_dirty=False,
            skip_rebuild=True,
            restore_branch=True,
        )
        with patch("eqemu_oracle.cli.update_plugin_repo", return_value={"rebuild": {"ran": False}}) as update_plugin_repo:
            exit_code = cli.update_plugin(args)

        self.assertEqual(exit_code, 0)
        update_plugin_repo.assert_called_once_with(
            remote="origin",
            branch="release",
            allow_dirty=False,
            skip_rebuild=True,
            restore_branch=True,
        )

    def test_install_global_forwards_to_installer(self) -> None:
        args = SimpleNamespace()
        with patch("eqemu_oracle.cli.install_global_plugin", return_value={"target_plugin_root": "C:/Users/test/plugins/eqemu-oracle"}) as installer:
            exit_code = cli.install_global(args)

        self.assertEqual(exit_code, 0)
        installer.assert_called_once_with()

    def test_main_accepts_install_command(self) -> None:
        with patch("eqemu_oracle.cli.install_global_plugin", return_value={"target_plugin_root": "C:/Users/test/plugins/eqemu-oracle"}) as installer:
            with patch.object(sys, "argv", ["eqemu_oracle.py", "install"]):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        installer.assert_called_once_with()
