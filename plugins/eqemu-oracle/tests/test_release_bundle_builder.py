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


if __name__ == "__main__":
    unittest.main()
