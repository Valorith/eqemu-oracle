from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.release_bundle import build_release_bundle, get_bundle_root  # noqa: E402


class ReleaseBundleBuilderTest(unittest.TestCase):
    def test_build_release_bundle_uses_current_plugin_version_and_skips_local_smoke(self) -> None:
        plugin_root = Path(__file__).resolve().parents[1]
        repo_root = Path(__file__).resolve().parents[3]
        plugin_manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        expected_version = plugin_manifest["version"]

        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = build_release_bundle(Path(temp_dir), repo_root=repo_root)

            self.assertEqual(archive_path.name, f"eqemu-oracle-v{expected_version}.zip")

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        bundle_root = get_bundle_root()
        self.assertIn(f"{bundle_root}/plugins/eqemu-oracle/.codex-plugin/plugin.json", names)
        self.assertIn(f"{bundle_root}/install.sh", names)
        self.assertFalse(any(name.startswith(f"{bundle_root}/dist-local-smoke/") for name in names))

    def test_build_release_bundle_does_not_include_its_own_output_file(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]

        with tempfile.TemporaryDirectory(dir=repo_root) as temp_dir:
            output_dir = Path(temp_dir)
            archive_path = build_release_bundle(output_dir, repo_root=repo_root)

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        bundle_root = get_bundle_root()
        self.assertNotIn(f"{bundle_root}/{output_dir.name}/{archive_path.name}", names)

    def test_build_release_bundle_excludes_private_local_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "repo"
            local_quests = repo_root / "plugins" / "eqemu-oracle" / "local-extensions" / "quests"
            local_quests.mkdir(parents=True, exist_ok=True)
            (local_quests / "_example.json").write_text('{"sources": []}\n', encoding="utf-8")
            (local_quests / "local.json").write_text('{"sources": [{"id": "private"}]}\n', encoding="utf-8")

            archive_path = build_release_bundle(Path(temp_dir) / "dist", repo_root=repo_root)

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        bundle_root = get_bundle_root()
        self.assertIn(f"{bundle_root}/plugins/eqemu-oracle/local-extensions/quests/_example.json", names)
        self.assertNotIn(f"{bundle_root}/plugins/eqemu-oracle/local-extensions/quests/local.json", names)

    def test_build_release_bundle_excludes_runtime_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "repo"
            runtime_root = repo_root / "plugins" / "eqemu-oracle" / "scripts"
            cache_root = runtime_root / "__pycache__"
            pytest_root = repo_root / "plugins" / "eqemu-oracle" / ".pytest_cache"
            runtime_root.mkdir(parents=True, exist_ok=True)
            cache_root.mkdir()
            pytest_root.mkdir()
            (repo_root / ".DS_Store").write_text("noise", encoding="utf-8")
            (cache_root / "module.cpython-312.pyc").write_bytes(b"bytecode")
            (runtime_root / "module.pyo").write_bytes(b"optimized")
            (pytest_root / "README.md").write_text("cache", encoding="utf-8")
            (runtime_root / "keep.py").write_text("print('ok')\n", encoding="utf-8")

            archive_path = build_release_bundle(Path(temp_dir) / "dist", repo_root=repo_root)

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        bundle_root = get_bundle_root()
        self.assertIn(f"{bundle_root}/plugins/eqemu-oracle/scripts/keep.py", names)
        self.assertNotIn(f"{bundle_root}/.DS_Store", names)
        self.assertFalse(any("__pycache__" in name for name in names))
        self.assertFalse(any(".pytest_cache" in name for name in names))
        self.assertFalse(any(name.endswith((".pyc", ".pyo")) for name in names))

    def test_build_release_bundle_excludes_local_remote_connection_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "repo"
            plugin_root = repo_root / "plugins" / "eqemu-oracle"
            plugin_root.mkdir(parents=True, exist_ok=True)
            (plugin_root / "server-connections.json").write_text('{"password":"secret"}\n', encoding="utf-8")
            remote_root = plugin_root / "remote-connections"
            remote_root.mkdir()
            (remote_root / "profile.json").write_text("secret\n", encoding="utf-8")
            staged_root = plugin_root / "staged-files"
            staged_root.mkdir()
            (staged_root / "edited.pl").write_text("local edit\n", encoding="utf-8")
            backup_root = plugin_root / "remote-backups"
            backup_root.mkdir()
            (backup_root / "npc.pl").write_text("backup\n", encoding="utf-8")
            (plugin_root / "scripts").mkdir()
            (plugin_root / "scripts" / "keep.py").write_text("print('ok')\n", encoding="utf-8")

            archive_path = build_release_bundle(Path(temp_dir) / "dist", repo_root=repo_root)

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        bundle_root = get_bundle_root()
        self.assertIn(f"{bundle_root}/plugins/eqemu-oracle/scripts/keep.py", names)
        self.assertNotIn(f"{bundle_root}/plugins/eqemu-oracle/server-connections.json", names)
        self.assertFalse(any("/remote-connections/" in name for name in names))
        self.assertFalse(any("/staged-files/" in name for name in names))
        self.assertFalse(any("/remote-backups/" in name for name in names))


if __name__ == "__main__":
    unittest.main()
