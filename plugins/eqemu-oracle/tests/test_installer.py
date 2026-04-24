from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import installer  # noqa: E402


def _seed_plugin_root(root: Path) -> None:
    (root / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "local-extensions").mkdir(parents=True, exist_ok=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": root.name,
                "version": "0.2.1",
                "interface": {
                    "category": "Coding",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (root / "scripts" / "eqemu_oracle.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "README.md").write_text("seed\n", encoding="utf-8")


class InstallerTest(unittest.TestCase):
    def test_install_global_plugin_falls_back_to_legacy_home_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)
                target_root = home / "plugins" / "eqemu-oracle"
                marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
                self.assertEqual(result["install_kind"], installer.LEGACY_HOME_INSTALL_KIND)
                self.assertIsNone(result["codex_cache_plugin_root"])
                self.assertIsNone(result["codex_config_path"])
                self.assertEqual(result["target_plugin_root"], str(target_root.resolve()))
                self.assertTrue((target_root / ".codex-plugin" / "plugin.json").exists())
                self.assertTrue((target_root / "README.md").exists())
                self.assertTrue((target_root / "local-extensions" / "quests" / "local.json").exists())
                self.assertTrue((target_root / "local-extensions" / "plugins" / "local.json").exists())
                self.assertIn("local-extensions/quests/local.json", result["seeded_local_extension_files"])
                self.assertIn("local-extensions/plugins/local.json", result["seeded_local_extension_files"])
                marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
                self.assertEqual(marketplace["name"], installer.MARKETPLACE_NAME)
                self.assertEqual(marketplace["plugins"][0]["name"], "eqemu-oracle")
                self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/eqemu-oracle")
                rebuild_command = run_mock.call_args.args[0]
                self.assertEqual(rebuild_command[1], str((target_root / "scripts" / "eqemu_oracle.py").resolve()))

    def test_install_global_plugin_uses_codex_desktop_marketplace_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            codex_root = home / ".codex" / ".tmp" / "plugins"
            marketplace_path = codex_root / ".agents" / "plugins" / "marketplace.json"
            marketplace_path.parent.mkdir(parents=True, exist_ok=True)
            (codex_root / "plugins").mkdir(parents=True, exist_ok=True)
            marketplace_path.write_text(
                json.dumps(
                    {
                        "name": "openai-curated",
                        "interface": {"displayName": "Codex official"},
                        "plugins": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)
                target_root = codex_root / "plugins" / "eqemu-oracle"
                cache_root = home / ".codex" / "plugins" / "cache" / "openai-curated" / "eqemu-oracle" / "local"
                config_path = home / ".codex" / "config.toml"
                self.assertEqual(result["install_kind"], installer.CODEX_DESKTOP_INSTALL_KIND)
                self.assertIsNone(result["codex_cache_plugin_root"])
                self.assertEqual(result["codex_config_path"], str(config_path.resolve()))
                self.assertEqual(result["target_plugin_root"], str(target_root.resolve()))
                self.assertFalse(cache_root.exists())
                marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
                self.assertEqual(marketplace["name"], "openai-curated")
                self.assertEqual(marketplace["plugins"][0]["name"], "eqemu-oracle")
                self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/eqemu-oracle")
                self.assertIn('[plugins."eqemu-oracle@openai-curated"]', config_path.read_text(encoding="utf-8"))
                self.assertIn("enabled = true", config_path.read_text(encoding="utf-8"))

    def test_install_global_plugin_migrates_legacy_local_overrides_into_codex_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            codex_root = home / ".codex" / ".tmp" / "plugins"
            marketplace_path = codex_root / ".agents" / "plugins" / "marketplace.json"
            marketplace_path.parent.mkdir(parents=True, exist_ok=True)
            (codex_root / "plugins").mkdir(parents=True, exist_ok=True)
            marketplace_path.write_text(
                json.dumps(
                    {
                        "name": "openai-curated",
                        "interface": {"displayName": "Codex official"},
                        "plugins": [],
                    }
                ),
                encoding="utf-8",
            )
            legacy_target = home / "plugins" / "eqemu-oracle"
            _seed_plugin_root(legacy_target)
            (legacy_target / "config" / "sources.local.toml").write_text("[docs]\nbranch = 'local'\n", encoding="utf-8")
            (legacy_target / "local-extensions" / "custom.json").write_text('{"ok": true}\n', encoding="utf-8")

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)
                target_root = codex_root / "plugins" / "eqemu-oracle"
                self.assertIn("config/sources.local.toml", result["migrated_paths"])
                self.assertIn("local-extensions", result["migrated_paths"])
                self.assertTrue((target_root / "config" / "sources.local.toml").exists())
                self.assertTrue((target_root / "local-extensions" / "custom.json").exists())

    def test_install_global_plugin_preserves_local_overrides_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            existing_target = home / "plugins" / "eqemu-oracle"
            _seed_plugin_root(existing_target)
            (existing_target / "config" / "sources.local.toml").write_text("[docs]\nbranch = 'local'\n", encoding="utf-8")
            (existing_target / "local-extensions" / "custom.json").write_text('{"ok": true}\n', encoding="utf-8")
            existing_local_quests = existing_target / "local-extensions" / "quests" / "local.json"
            existing_local_quests.parent.mkdir(parents=True, exist_ok=True)
            existing_local_quests.write_text('{"sources": [{"id": "mine"}]}\n', encoding="utf-8")

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)
                target_root = home / "plugins" / "eqemu-oracle"
                self.assertIn("config/sources.local.toml", result["restored_paths"])
                self.assertIn("local-extensions", result["restored_paths"])
                self.assertTrue((target_root / "config" / "sources.local.toml").exists())
                self.assertTrue((target_root / "local-extensions" / "custom.json").exists())
                self.assertEqual(
                    json.loads((target_root / "local-extensions" / "quests" / "local.json").read_text(encoding="utf-8"))["sources"][0]["id"],
                    "mine",
                )
                self.assertIn("local-extensions/plugins/local.json", result["seeded_local_extension_files"])
                self.assertNotIn("local-extensions/quests/local.json", result["seeded_local_extension_files"])


if __name__ == "__main__":
    unittest.main()
