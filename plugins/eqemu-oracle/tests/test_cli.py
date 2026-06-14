from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
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

    def test_build_bundle_forwards_output_dir(self) -> None:
        args = SimpleNamespace(output_dir=Path("/tmp/dist"))
        with patch("eqemu_oracle.cli.build_release_bundle", return_value=Path("/tmp/dist/eqemu-oracle-v1.0.1.zip")) as build_release_bundle:
            exit_code = cli.build_bundle(args)

        self.assertEqual(exit_code, 0)
        build_release_bundle.assert_called_once_with(output_dir=Path("/tmp/dist"))

    def test_tool_command_runs_read_tool(self) -> None:
        server = Mock()
        server._handle_tool.return_value = {
            "content": [{"type": "text", "text": "schema markdown"}],
            "structuredContent": {"table": "spawn2"},
            "isError": False,
        }
        args = SimpleNamespace(name="get_db_table", args='{"table_name":"spawn2"}', markdown=False)

        with patch("eqemu_oracle.cli.McpServer", return_value=server):
            exit_code = cli.run_tool(args)

        self.assertEqual(exit_code, 0)
        server._handle_tool.assert_called_once_with("get_db_table", {"table_name": "spawn2"})

    def test_tool_command_rejects_invalid_json(self) -> None:
        args = SimpleNamespace(name="get_db_table", args="{", markdown=False)

        with patch("eqemu_oracle.cli.McpServer") as server:
            exit_code = cli.run_tool(args)

        self.assertEqual(exit_code, 2)
        server.assert_not_called()

    def test_main_accepts_tool_command(self) -> None:
        server = Mock()
        server._handle_tool.return_value = {
            "content": [{"type": "text", "text": "schema markdown"}],
            "structuredContent": {"table": "spawn2"},
            "isError": False,
        }

        with patch("eqemu_oracle.cli.McpServer", return_value=server):
            with patch.object(sys, "argv", ["eqemu_oracle.py", "tool", "get_db_table", "--args", '{"table_name":"spawn2"}']):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        server._handle_tool.assert_called_once_with("get_db_table", {"table_name": "spawn2"})

    def test_main_accepts_remote_onboarding_command(self) -> None:
        with patch("eqemu_oracle.cli.remote_onboarding_payload", return_value={"recommended_setup_command": "setup"}):
            with patch.object(sys, "argv", ["eqemu_oracle.py", "remote", "onboarding"]):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)

    def test_remote_setup_with_password_env_requires_host_and_username(self) -> None:
        with patch.object(sys, "argv", ["eqemu_oracle.py", "remote", "setup", "--password-env", "EQEMU_TEST_PASSWORD"]):
            exit_code = cli.main()

        self.assertEqual(exit_code, 2)

    def test_main_accepts_remote_map_command(self) -> None:
        with patch("eqemu_oracle.cli.map_remote_eqemu_server", return_value={"entries": []}) as mapper:
            with patch.object(
                sys,
                "argv",
                [
                    "eqemu_oracle.py",
                    "remote",
                    "map",
                    "live",
                    "--scope",
                    "zone",
                    "--zone",
                    "qeynos",
                    "--max-depth",
                    "4",
                    "--limit",
                    "250",
                ],
            ):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        mapper.assert_called_once_with("live", remote_path=None, scope="zone", zone="qeynos", max_depth=4, limit=250)

    def test_main_accepts_remote_trust_cert_command(self) -> None:
        fingerprint = "A" * 64
        with patch("eqemu_oracle.cli.trust_ftps_certificate", return_value={"trusted": True}) as trust:
            with patch.object(
                sys,
                "argv",
                [
                    "eqemu_oracle.py",
                    "remote",
                    "trust-cert",
                    "live",
                    "--confirm-trust",
                    "--confirm-sha256",
                    fingerprint,
                ],
            ):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        trust.assert_called_once_with("live", confirm_trust=True, confirm_sha256=fingerprint)

    def test_main_accepts_remote_upload_allow_create(self) -> None:
        with patch("eqemu_oracle.cli.upload_staged_file", return_value={"uploaded": True}) as upload:
            with patch.object(
                sys,
                "argv",
                [
                    "eqemu_oracle.py",
                    "remote",
                    "upload",
                    "live",
                    "C:/staged/textFile.txt",
                    "--remote-path",
                    "/quests/global/textFile.txt",
                    "--confirm-write",
                    "--confirm-remote-path",
                    "/quests/global/textFile.txt",
                    "--allow-create",
                ],
            ):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        upload.assert_called_once_with(
            "live",
            local_path="C:\\staged\\textFile.txt" if sys.platform == "win32" else "C:/staged/textFile.txt",
            remote_path="/quests/global/textFile.txt",
            confirm_write=True,
            confirm_remote_path="/quests/global/textFile.txt",
            create_backup=True,
            allow_create=True,
            allow_remote_changed=False,
            max_backup_bytes=5 * 1024 * 1024,
        )

    def test_main_accepts_remote_delete_command(self) -> None:
        fingerprint = "B" * 64
        with patch("eqemu_oracle.cli.delete_remote_file", return_value={"deleted": True}) as delete:
            with patch.object(
                sys,
                "argv",
                [
                    "eqemu_oracle.py",
                    "remote",
                    "delete",
                    "live",
                    "/quests/global/textFile.txt",
                    "--confirm-delete",
                    "--confirm-remote-path",
                    "/quests/global/textFile.txt",
                    "--confirm-remote-sha256",
                    fingerprint,
                    "--max-backup-bytes",
                    "2048",
                ],
            ):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        delete.assert_called_once_with(
            "live",
            remote_path="/quests/global/textFile.txt",
            confirm_delete=True,
            confirm_remote_path="/quests/global/textFile.txt",
            confirm_remote_sha256=fingerprint,
            max_backup_bytes=2048,
        )

    def test_main_accepts_remote_gc_command(self) -> None:
        with patch("eqemu_oracle.cli.garbage_collect_write_history", return_value={"applied": False}) as gc:
            with patch.object(
                sys,
                "argv",
                [
                    "eqemu_oracle.py",
                    "remote",
                    "gc",
                    "live",
                    "--apply",
                    "--keep-operations",
                    "3",
                    "--max-bytes",
                    "100",
                    "--no-prune-orphans",
                ],
            ):
                exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        gc.assert_called_once_with(
            "live",
            apply=True,
            confirm_write=False,
            prune_orphans=False,
            max_operations=3,
            max_bytes=100,
        )
