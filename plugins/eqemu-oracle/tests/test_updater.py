from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import updater  # noqa: E402


class UpdaterTest(unittest.TestCase):
    def test_update_plugin_repo_refuses_dirty_worktree(self) -> None:
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/Valorith/eqemu-oracle.git",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): " M README.md",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            return responses[tuple(args)]

        with patch("eqemu_oracle.updater._git", side_effect=fake_git):
            with self.assertRaisesRegex(RuntimeError, "dirty worktree"):
                updater.update_plugin_repo(Path("C:/repo"))

    def test_update_plugin_repo_fetches_pulls_and_rebuilds(self) -> None:
        calls: list[tuple[str, ...]] = []
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/Valorith/eqemu-oracle.git",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): ["abc123", "def456"],
            ("fetch", "origin"): "",
            ("pull", "--ff-only", "origin", "main"): "Updating abc123..def456",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            calls.append(tuple(args))
            value = responses[tuple(args)]
            if isinstance(value, list):
                return value.pop(0)
            return value

        with patch("eqemu_oracle.updater._git", side_effect=fake_git):
            with patch("eqemu_oracle.updater.rebuild_committed_dataset", return_value={"counts": {"quest-api": 1}}):
                result = updater.update_plugin_repo(Path("C:/repo"))

        self.assertEqual(result["before_commit"], "abc123")
        self.assertEqual(result["after_commit"], "def456")
        self.assertTrue(result["code_changed"])
        self.assertTrue(result["rebuild"]["ran"])
        self.assertEqual(result["rebuild"]["manifest"]["counts"]["quest-api"], 1)
        self.assertIn(("fetch", "origin"), calls)
        self.assertIn(("pull", "--ff-only", "origin", "main"), calls)

    def test_update_plugin_repo_skips_rebuild_when_requested(self) -> None:
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/Valorith/eqemu-oracle.git",
            ("rev-parse", "--abbrev-ref", "HEAD"): "feature/test",
            ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): ["abc123", "abc123"],
            ("fetch", "origin"): "",
            ("pull", "--ff-only", "origin", "feature/test"): "Already up to date.",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            value = responses[tuple(args)]
            if isinstance(value, list):
                return value.pop(0)
            return value

        with patch("eqemu_oracle.updater._git", side_effect=fake_git):
            with patch("eqemu_oracle.updater.rebuild_committed_dataset") as rebuild_mock:
                result = updater.update_plugin_repo(Path("C:/repo"), skip_rebuild=True)

        rebuild_mock.assert_not_called()
        self.assertFalse(result["code_changed"])
        self.assertFalse(result["rebuild"]["ran"])
        self.assertEqual(result["branch"], "feature/test")


if __name__ == "__main__":
    unittest.main()
