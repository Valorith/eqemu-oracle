from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path


class ReleaseBundleTest(unittest.TestCase):
    def test_local_smoke_bundle_matches_current_plugin_version(self) -> None:
        plugin_root = Path(__file__).resolve().parents[1]
        repo_root = Path(__file__).resolve().parents[3]
        plugin_manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        launcher_bytes = (plugin_root / "scripts" / "eqemu_oracle_launcher.cmd").read_bytes()
        install_cmd = (repo_root / "install.cmd").read_bytes()
        install_sh = (repo_root / "install.sh").read_bytes()
        expected_version = plugin_manifest["version"]
        bundle_root = f"eqemu-oracle-v{expected_version}"
        archive_path = repo_root / "dist-local-smoke" / f"{bundle_root}.zip"

        self.assertTrue(archive_path.exists(), f"Expected local smoke bundle at {archive_path}")

        with zipfile.ZipFile(archive_path) as archive:
            plugin_manifest_path = f"{bundle_root}/plugins/eqemu-oracle/.codex-plugin/plugin.json"
            launcher_path = f"{bundle_root}/plugins/eqemu-oracle/scripts/eqemu_oracle_launcher.cmd"
            install_cmd_path = f"{bundle_root}/install.cmd"
            install_sh_path = f"{bundle_root}/install.sh"
            bundle_manifest = json.loads(archive.read(plugin_manifest_path).decode("utf-8"))
            bundled_launcher = archive.read(launcher_path)
            bundled_install_cmd = archive.read(install_cmd_path)
            bundled_install_sh = archive.read(install_sh_path)
            launcher_info = archive.getinfo(launcher_path)
            install_sh_info = archive.getinfo(install_sh_path)

        self.assertEqual(bundle_manifest["version"], expected_version)
        self.assertEqual(bundled_launcher, launcher_bytes)
        self.assertEqual(bundled_install_cmd, install_cmd)
        self.assertEqual(bundled_install_sh, install_sh)
        self.assertEqual(launcher_info.create_system, 3)
        self.assertTrue((launcher_info.external_attr >> 16) & 0o111)
        self.assertEqual(install_sh_info.create_system, 3)
        self.assertTrue((install_sh_info.external_attr >> 16) & 0o111)


if __name__ == "__main__":
    unittest.main()
