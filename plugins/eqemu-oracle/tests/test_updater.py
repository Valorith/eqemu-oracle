from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import updater  # noqa: E402


def _valid_codex_config_patch():
    return patch("eqemu_oracle.updater.validate_codex_config", return_value="/home/test/.codex/config.toml")


class UpdaterTest(unittest.TestCase):
    def test_update_plugin_repo_refuses_invalid_codex_config_before_git_changes(self) -> None:
        with patch("eqemu_oracle.updater.validate_codex_config", side_effect=RuntimeError("duplicate key")):
            with patch("eqemu_oracle.updater._git") as git_mock:
                with self.assertRaisesRegex(RuntimeError, "duplicate key"):
                    updater.update_plugin_repo(Path("C:/repo"))

        git_mock.assert_not_called()

    def test_update_plugin_repo_refuses_non_oracle_remote_before_fetch(self) -> None:
        calls: list[tuple[str, ...]] = []
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/openai/plugins.git",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            calls.append(tuple(args))
            return responses[tuple(args)]

        with patch("eqemu_oracle.updater._git", side_effect=fake_git), _valid_codex_config_patch():
            with self.assertRaisesRegex(RuntimeError, "not an eqemu-oracle repository"):
                updater.update_plugin_repo(Path("C:/repo"))

        self.assertNotIn(("fetch", "origin"), calls)

    def test_update_plugin_repo_accepts_forked_oracle_remote(self) -> None:
        self.assertEqual(updater._remote_repo_name("git@github.com:example/eqemu-oracle.git"), "eqemu-oracle")
        self.assertEqual(updater._remote_repo_name("https://github.com/example/eqemu-oracle"), "eqemu-oracle")

    def test_update_plugin_repo_refuses_dirty_worktree(self) -> None:
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/Valorith/eqemu-oracle.git",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): " M README.md",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            return responses[tuple(args)]

        with patch("eqemu_oracle.updater._git", side_effect=fake_git), _valid_codex_config_patch() as validate_config:
            with self.assertRaisesRegex(RuntimeError, "dirty worktree"):
                updater.update_plugin_repo(Path("C:/repo"))

        validate_config.assert_called_once_with()

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

        with patch("eqemu_oracle.updater._git", side_effect=fake_git), _valid_codex_config_patch() as validate_config:
            with patch("eqemu_oracle.updater.rebuild_committed_dataset", return_value={"counts": {"quest-api": 1}}):
                result = updater.update_plugin_repo(Path("C:/repo"))

        self.assertEqual(result["before_commit"], "abc123")
        self.assertEqual(result["after_commit"], "def456")
        self.assertTrue(result["code_changed"])
        self.assertTrue(result["rebuild"]["ran"])
        self.assertEqual(result["rebuild"]["manifest"]["counts"]["quest-api"], 1)
        self.assertEqual(result["codex_config_validation"]["preflight_path"], "/home/test/.codex/config.toml")
        self.assertEqual(result["codex_config_validation"]["postflight_path"], "/home/test/.codex/config.toml")
        self.assertIn(("fetch", "origin"), calls)
        self.assertIn(("pull", "--ff-only", "origin", "main"), calls)
        self.assertEqual(validate_config.call_count, 2)

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

        with patch("eqemu_oracle.updater._git", side_effect=fake_git), _valid_codex_config_patch() as validate_config:
            with patch("eqemu_oracle.updater.rebuild_committed_dataset") as rebuild_mock:
                result = updater.update_plugin_repo(Path("C:/repo"), skip_rebuild=True)

        rebuild_mock.assert_not_called()
        self.assertFalse(result["code_changed"])
        self.assertFalse(result["rebuild"]["ran"])
        self.assertEqual(result["branch"], "feature/test")
        self.assertEqual(validate_config.call_count, 2)

    def test_update_plugin_repo_switches_to_requested_branch_before_pull(self) -> None:
        calls: list[tuple[str, ...]] = []
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/Valorith/eqemu-oracle.git",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): "",
            ("fetch", "origin"): "",
            ("show-ref", "--verify", "refs/heads/release"): "",
            ("switch", "release"): "",
            ("rev-parse", "HEAD"): ["fed111", "fed222"],
            ("pull", "--ff-only", "origin", "release"): "Updating fed111..fed222",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            calls.append(tuple(args))
            value = responses[tuple(args)]
            if isinstance(value, list):
                return value.pop(0)
            return value

        with patch("eqemu_oracle.updater._git", side_effect=fake_git), _valid_codex_config_patch() as validate_config:
            with patch("eqemu_oracle.updater.rebuild_committed_dataset", return_value={"counts": {"quest-api": 1}}):
                result = updater.update_plugin_repo(Path("C:/repo"), branch="release")

        self.assertEqual(result["current_branch"], "main")
        self.assertEqual(result["checked_out_branch"], "release")
        self.assertTrue(result["switched_branches"])
        self.assertIn(("switch", "release"), calls)
        self.assertLess(calls.index(("switch", "release")), calls.index(("pull", "--ff-only", "origin", "release")))
        self.assertEqual(validate_config.call_count, 2)

    def test_update_plugin_repo_can_restore_previous_branch(self) -> None:
        calls: list[tuple[str, ...]] = []
        responses = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("config", "--get", "remote.origin.url"): "https://github.com/Valorith/eqemu-oracle.git",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): "",
            ("fetch", "origin"): "",
            ("show-ref", "--verify", "refs/heads/release"): "",
            ("switch", "release"): "",
            ("switch", "main"): "",
            ("rev-parse", "HEAD"): ["abc123", "def456"],
            ("pull", "--ff-only", "origin", "release"): "Updating abc123..def456",
        }

        def fake_git(args: list[str], cwd: Path) -> str:
            calls.append(tuple(args))
            value = responses[tuple(args)]
            if isinstance(value, list):
                return value.pop(0)
            return value

        with patch("eqemu_oracle.updater._git", side_effect=fake_git), _valid_codex_config_patch() as validate_config:
            with patch("eqemu_oracle.updater.rebuild_committed_dataset", return_value={"counts": {"quest-api": 1}}):
                result = updater.update_plugin_repo(Path("C:/repo"), branch="release", restore_branch=True)

        self.assertTrue(result["restore_branch_requested"])
        self.assertTrue(result["restored_branch"])
        self.assertIn(("switch", "main"), calls)
        self.assertLess(calls.index(("pull", "--ff-only", "origin", "release")), calls.index(("switch", "main")))
        self.assertEqual(validate_config.call_count, 2)


if __name__ == "__main__":
    unittest.main()
