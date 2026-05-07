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
                local_marketplace_root = home / ".codex" / "local-marketplaces" / "user-local"
                local_marketplace_path = local_marketplace_root / ".agents" / "plugins" / "marketplace.json"
                target_root = local_marketplace_root / "plugins" / "eqemu-oracle"
                cache_root = home / ".codex" / "plugins" / "cache" / "user-local" / "eqemu-oracle" / "local"
                config_path = home / ".codex" / "config.toml"
                self.assertEqual(result["install_kind"], installer.CODEX_DESKTOP_INSTALL_KIND)
                self.assertEqual(result["codex_cache_plugin_root"], str(cache_root.resolve()))
                self.assertEqual(result["codex_config_path"], str(config_path.resolve()))
                self.assertEqual(result["target_plugin_root"], str(target_root.resolve()))
                self.assertTrue(cache_root.exists())
                self.assertEqual(result["codex_cache_activation_copy"]["target_root"], str(target_root.resolve()))
                self.assertTrue((cache_root / ".codex-plugin" / "plugin.json").exists())
                marketplace = json.loads(local_marketplace_path.read_text(encoding="utf-8"))
                self.assertEqual(marketplace["name"], "user-local")
                self.assertEqual(marketplace["plugins"][0]["name"], "eqemu-oracle")
                self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/eqemu-oracle")
                self.assertIn('[plugins."eqemu-oracle@user-local"]', config_path.read_text(encoding="utf-8"))
                self.assertIn("enabled = true", config_path.read_text(encoding="utf-8"))
                self.assertIn('[mcp_servers."eqemu_oracle"]', config_path.read_text(encoding="utf-8"))
                self.assertIn('[marketplaces."user-local"]', config_path.read_text(encoding="utf-8"))
                self.assertIn('source_type = "local"', config_path.read_text(encoding="utf-8"))
                self.assertIn(str(local_marketplace_root.resolve()).replace("\\", "\\\\"), config_path.read_text(encoding="utf-8"))
                self.assertIn(str((target_root / "scripts" / "eqemu_oracle.py").resolve()).replace("\\", "\\\\"), config_path.read_text(encoding="utf-8"))
                self.assertIn(str(target_root.resolve()).replace("\\", "\\\\"), config_path.read_text(encoding="utf-8"))

    def test_install_global_plugin_uses_stable_local_marketplace_when_official_marketplace_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            (home / ".codex").mkdir(parents=True, exist_ok=True)

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)

            local_marketplace_root = home / ".codex" / "local-marketplaces" / "user-local"
            target_root = local_marketplace_root / "plugins" / "eqemu-oracle"
            self.assertEqual(result["marketplace_path"], str((local_marketplace_root / ".agents" / "plugins" / "marketplace.json").resolve()))
            self.assertEqual(result["target_plugin_root"], str(target_root.resolve()))
            self.assertTrue((target_root / ".codex-plugin" / "plugin.json").exists())

    def test_install_global_plugin_installs_git_checkout_when_source_is_git(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            repo_root = Path(temp_dir) / "source" / "repo"
            source_root = repo_root / "plugins" / "eqemu-oracle"
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
            git_source = {
                "repo_root": repo_root,
                "plugin_subpath": Path("plugins") / "eqemu-oracle",
                "remote_url": "https://github.com/Valorith/eqemu-oracle.git",
                "branch": "main",
            }

            def fake_clone(_git_source: dict[str, object], checkout_root: Path, _plugins_root: Path) -> None:
                _seed_plugin_root(checkout_root / "plugins" / "eqemu-oracle")

            with patch("eqemu_oracle.installer._source_git_checkout", return_value=git_source):
                with patch("eqemu_oracle.installer._clone_git_checkout", side_effect=fake_clone):
                    with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                        run_mock.return_value.returncode = 0
                        run_mock.return_value.stdout = ""
                        run_mock.return_value.stderr = ""
                        result = installer.install_global_plugin(home=home, source_plugin_root=source_root)

            local_marketplace_root = home / ".codex" / "local-marketplaces" / "user-local"
            local_marketplace_path = local_marketplace_root / ".agents" / "plugins" / "marketplace.json"
            checkout_root = local_marketplace_root / "plugins" / "eqemu-oracle"
            target_root = checkout_root / "plugins" / "eqemu-oracle"
            marketplace = json.loads(local_marketplace_path.read_text(encoding="utf-8"))
            self.assertEqual(result["install_strategy"], "git-checkout")
            self.assertEqual(result["checkout_root"], str(checkout_root.resolve()))
            self.assertEqual(result["target_plugin_root"], str(target_root.resolve()))
            self.assertEqual(result["marketplace_source_path"], "./plugins/eqemu-oracle/plugins/eqemu-oracle")
            self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/eqemu-oracle/plugins/eqemu-oracle")
            self.assertEqual(result["git"]["plugin_subpath"], "plugins/eqemu-oracle")
            self.assertEqual(result["git"]["remote_url"], "https://github.com/Valorith/eqemu-oracle.git")

    def test_install_global_plugin_deduplicates_marketplace_registrations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            codex_root = home / ".codex" / ".tmp" / "plugins"
            marketplace_path = codex_root / ".agents" / "plugins" / "marketplace.json"
            legacy_marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
            marketplace_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_marketplace_path.parent.mkdir(parents=True, exist_ok=True)
            (codex_root / "plugins").mkdir(parents=True, exist_ok=True)
            marketplace_path.write_text(
                json.dumps(
                    {
                        "name": "openai-curated",
                        "interface": {"displayName": "Codex official"},
                        "plugins": [
                            {"name": "eqemu-oracle", "source": {"path": "./plugins/old-eqemu-oracle"}},
                            {"name": "EQEmu Oracle", "source": {"path": "./plugins/eqemu-oracle"}},
                            {"name": "other-plugin", "source": {"path": "./plugins/other-plugin"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            legacy_marketplace_path.write_text(
                json.dumps(
                    {
                        "name": "user-local",
                        "interface": {"displayName": "Local Plugins"},
                        "plugins": [
                            {"name": "eqemu-oracle", "source": {"path": "./plugins/eqemu-oracle"}},
                            {"name": "other-legacy", "source": {"path": "./plugins/other-legacy"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)

                local_marketplace_path = home / ".codex" / "local-marketplaces" / "user-local" / ".agents" / "plugins" / "marketplace.json"
                local_marketplace = json.loads(local_marketplace_path.read_text(encoding="utf-8"))
                local_plugin_names = [entry["name"] for entry in local_marketplace["plugins"]]
                self.assertEqual(local_plugin_names.count("eqemu-oracle"), 1)
                self.assertEqual(result["replaced_active_marketplace_entries"], 0)

                official_marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
                official_plugin_names = [entry["name"] for entry in official_marketplace["plugins"]]
                self.assertNotIn("eqemu-oracle", official_plugin_names)
                self.assertNotIn("EQEmu Oracle", official_plugin_names)
                self.assertIn("other-plugin", official_plugin_names)

                legacy_marketplace = json.loads(legacy_marketplace_path.read_text(encoding="utf-8"))
                legacy_plugin_names = [entry["name"] for entry in legacy_marketplace["plugins"]]
                self.assertNotIn("eqemu-oracle", legacy_plugin_names)
                self.assertIn("other-legacy", legacy_plugin_names)
                removed_counts = sorted(entry["removed_entries"] for entry in result["pruned_inactive_marketplace_entries"])
                self.assertEqual(removed_counts, [1, 2])

    def test_install_global_plugin_prunes_stale_codex_cache_install_after_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            source_example = source_root / "local-extensions" / "quests" / "_example.json"
            source_example.parent.mkdir(parents=True, exist_ok=True)
            source_example.write_text('{"example": true}\n', encoding="utf-8")
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
            stale_cache_root = home / ".codex" / "plugins" / "cache" / "openai-curated" / "eqemu-oracle" / "local"
            _seed_plugin_root(stale_cache_root)
            (stale_cache_root / "config" / "sources.local.toml").write_text("[docs]\nbranch = 'cache-local'\n", encoding="utf-8")
            (stale_cache_root / "local-extensions" / "custom.json").write_text('{"cache": true}\n', encoding="utf-8")
            hashed_cache_root = home / ".codex" / "plugins" / "cache" / "openai-curated" / "eqemu-oracle" / "6807e4de"
            _seed_plugin_root(hashed_cache_root)

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)
                target_root = home / ".codex" / "local-marketplaces" / "user-local" / "plugins" / "eqemu-oracle"

                activation_copy = home / ".codex" / "plugins" / "cache" / "user-local" / "eqemu-oracle" / "local"
                self.assertTrue(activation_copy.exists())
                self.assertTrue((activation_copy / ".codex-plugin" / "plugin.json").exists())
                self.assertFalse(hashed_cache_root.exists())
                self.assertFalse(stale_cache_root.exists())
                self.assertTrue((target_root / "local-extensions" / "quests" / "_example.json").exists())
                self.assertTrue((target_root / "config" / "sources.local.toml").exists())
                self.assertTrue((target_root / "local-extensions" / "custom.json").exists())
                migrated_path_sets = [entry["migrated_paths"] for entry in result["pruned_stale_cache_installs"]]
                self.assertIn(["config/sources.local.toml", "local-extensions"], migrated_path_sets)

    def test_enable_codex_plugin_repairs_duplicate_config_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            config_path = codex_root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'model = "gpt-5"',
                        "",
                        '[plugins."eqemu-oracle@openai-curated"]',
                        "enabled = false",
                        'channel = "stable"',
                        "",
                        '[plugins."eqemu-oracle@openai-curated"]',
                        "enabled = true",
                        'channel = "duplicate"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated")
            text = config_path.read_text(encoding="utf-8")
            self.assertEqual(text.count('[plugins."eqemu-oracle@openai-curated"]'), 1)
            self.assertEqual(text.count("enabled = true"), 1)
            self.assertNotIn("enabled = false", text)
            self.assertIn('channel = "stable"', text)
            self.assertNotIn('channel = "duplicate"', text)

    def test_enable_codex_plugin_writes_direct_mcp_server_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            target_root = Path(temp_dir) / "installed" / "eqemu-oracle"
            _seed_plugin_root(target_root)
            config_path = codex_root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        '[mcp_servers."eqemu-oracle"]',
                        'command = "old"',
                        'args = ["old"]',
                        'cwd = "old"',
                        "",
                        "[mcp_servers.eqemu_oracle]",
                        'command = "duplicate"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated", target_root)

            text = config_path.read_text(encoding="utf-8")
            self.assertNotIn('[mcp_servers."eqemu-oracle"]', text)
            self.assertEqual(text.count('[mcp_servers."eqemu_oracle"]'), 1)
            self.assertNotIn('command = "old"', text)
            self.assertNotIn('command = "duplicate"', text)
            self.assertIn(str((target_root / "scripts" / "eqemu_oracle.py").resolve()).replace("\\", "\\\\"), text)
            self.assertIn(str(Path(sys.executable).resolve()).replace("\\", "\\\\"), text)
            self.assertIn('"mcp-serve"', text)
            self.assertIn(str(target_root.resolve()).replace("\\", "\\\\"), text)
            installer.validate_codex_config(home)

    def test_enable_codex_plugin_writes_local_marketplace_source_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            marketplace_root = codex_root / ".tmp" / "plugins"
            marketplace_root.mkdir(parents=True, exist_ok=True)
            config_path = codex_root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[marketplaces.openai-curated]",
                        'source_type = "remote"',
                        'source = "old"',
                        "",
                        '[marketplaces."openai-curated"]',
                        'source_type = "local"',
                        'source = "duplicate"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated", marketplace_root=marketplace_root)

            text = config_path.read_text(encoding="utf-8")
            self.assertEqual(text.count('[marketplaces."openai-curated"]'), 1)
            self.assertNotIn('source = "old"', text)
            self.assertNotIn('source = "duplicate"', text)
            self.assertIn('source_type = "local"', text)
            self.assertIn(str(marketplace_root.resolve()).replace("\\", "\\\\"), text)
            installer.validate_codex_config(home)

    def test_enable_codex_plugin_repairs_duplicate_enabled_key_spellings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            config_path = codex_root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        '[plugins."eqemu-oracle@openai-curated"]',
                        "enabled = false",
                        '"enabled" = true',
                        'channel = "stable"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated")
            text = config_path.read_text(encoding="utf-8")
            self.assertEqual(text.count("enabled = true"), 1)
            self.assertNotIn("enabled = false", text)
            self.assertNotIn('"enabled" = true', text)
            installer.validate_codex_config(home)

    def test_enable_codex_plugin_refuses_to_write_invalid_codex_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            config_path = codex_root / "config.toml"
            original_text = "\n".join(
                [
                    "[profiles.default]",
                    'model = "gpt-5"',
                    'model = "gpt-5.1"',
                    "",
                    '[plugins."eqemu-oracle@openai-curated"]',
                    "enabled = false",
                    "",
                ]
            )
            config_path.write_text(original_text, encoding="utf-8")

            with self.assertRaisesRegex(installer.CodexConfigError, "Refusing to write invalid Codex config TOML"):
                installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated")

            self.assertEqual(config_path.read_text(encoding="utf-8"), original_text)

    def test_enable_codex_plugin_replaces_stale_marketplace_config_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            config_path = codex_root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        '[plugins."eqemu-oracle@user-local"]',
                        "enabled = false",
                        'channel = "stable"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated")
            text = config_path.read_text(encoding="utf-8")
            self.assertNotIn('[plugins."eqemu-oracle@user-local"]', text)
            self.assertEqual(text.count('[plugins."eqemu-oracle@openai-curated"]'), 1)
            self.assertEqual(text.count("enabled = true"), 1)
            self.assertNotIn("enabled = false", text)
            self.assertIn('channel = "stable"', text)

    def test_enable_codex_plugin_normalizes_single_quoted_config_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            codex_root = home / ".codex"
            codex_root.mkdir(parents=True, exist_ok=True)
            config_path = codex_root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[plugins.'eqemu-oracle@openai-curated']",
                        "enabled = true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            installer._enable_codex_plugin(home, "eqemu-oracle", "openai-curated")
            text = config_path.read_text(encoding="utf-8")
            self.assertNotIn("[plugins.'eqemu-oracle@openai-curated']", text)
            self.assertEqual(text.count('[plugins."eqemu-oracle@openai-curated"]'), 1)
            installer.validate_codex_config(home)

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
                target_root = home / ".codex" / "local-marketplaces" / "user-local" / "plugins" / "eqemu-oracle"
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

    def test_install_global_plugin_rebuilds_overlay_when_local_extensions_are_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_root = Path(temp_dir) / "source" / "eqemu-oracle"
            _seed_plugin_root(source_root)
            local_quests = source_root / "local-extensions" / "quests" / "local.json"
            local_quests.parent.mkdir(parents=True, exist_ok=True)
            local_quests.write_text('{"sources": [{"id": "mine", "url": "file:///quests"}]}\n', encoding="utf-8")

            with patch("eqemu_oracle.installer.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                result = installer.install_global_plugin(home=home, source_plugin_root=source_root)
                rebuild_command = run_mock.call_args.args[0]
                self.assertEqual(rebuild_command[-2:], ["--mode", "overlay"])
                self.assertEqual(result["rebuild"]["command"][-2:], ["--mode", "overlay"])


if __name__ == "__main__":
    unittest.main()
