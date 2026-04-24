from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import find_stale_schema_extensions, prune_stale_schema_extensions, validate_extension_overlays  # noqa: E402
from eqemu_oracle.extensions import load_domain_extensions, merge_records, merge_source_records  # noqa: E402
from eqemu_oracle.extensions import ExtensionValidationError  # noqa: E402
from eqemu_oracle.utils import dump_json, load_json  # noqa: E402


class MergeRecordsTest(unittest.TestCase):
    def test_overlay_wins_and_tracks_provenance(self) -> None:
        base = [{"id": "foo", "title": "Base", "tags": ["a"]}]
        repo_ext = [{"id": "foo", "title": "Repo", "_extension_file": "repo.json"}]
        local_ext = [{"id": "foo", "title": "Local", "_extension_file": "local.json"}]
        merged = merge_records(base, repo_ext, local_ext)
        self.assertEqual(merged[0]["title"], "Local")
        self.assertTrue(merged[0]["extension_flags"]["has_repo_extension"])
        self.assertTrue(merged[0]["extension_flags"]["has_local_extension"])

    def test_disable_removes_record(self) -> None:
        base = [{"id": "foo", "title": "Base"}]
        merged = merge_records(base, [], [{"id": "foo", "mode": "disable", "_extension_file": "local.json"}])
        self.assertEqual(merged, [])

    def test_augment_creates_new_record_when_base_is_missing(self) -> None:
        repo_ext = [
            {
                "id": "character_offline_transactions",
                "table": "character_offline_transactions",
                "title": "character_offline_transactions",
                "mode": "augment",
                "_extension_file": "schema_extension.json",
            }
        ]
        merged = merge_records([], repo_ext, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["table"], "character_offline_transactions")
        self.assertEqual(merged[0]["provenance"]["effective_source"], "repo_extension")
        self.assertTrue(merged[0]["extension_flags"]["has_repo_extension"])
        self.assertFalse(merged[0]["extension_flags"]["has_local_extension"])

    def test_validate_extension_overlays_reports_user_facing_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_root = root / "base"
            repo_root = root / "extensions"
            local_root = root / "local-extensions"

            dump_json(base_root / "quest-api" / "methods.json", [])
            dump_json(base_root / "quest-api" / "events.json", [])
            dump_json(base_root / "quest-api" / "constants.json", [])
            dump_json(base_root / "schema" / "index.json", [])
            dump_json(base_root / "docs" / "pages.json", [])
            dump_json(
                repo_root / "schema" / "broken.json",
                {"tables": [{"id": "bad_table", "table": "bad_table", "mode": "create"}]},
            )
            (local_root / "schema").mkdir(parents=True, exist_ok=True)

            with self.assertRaises(ExtensionValidationError) as ctx:
                validate_extension_overlays(base_root, repo_root, local_root)

            message = str(ctx.exception)
            self.assertIn("extension validation failed", message.lower())
            self.assertIn("schema:", message)
            self.assertIn("broken.json", message)
            self.assertIn("unsupported mode 'create'", message)

    def test_example_extension_files_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dump_json(
                root / "schema" / "_example.json",
                {"tables": [{"id": "example_table", "table": "example_table", "mode": "augment"}]},
            )
            dump_json(
                root / "schema" / "real_extension.json",
                {"tables": [{"id": "real_table", "table": "real_table", "mode": "augment"}]},
            )

            loaded = load_domain_extensions(root, "schema")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["id"], "real_table")
            self.assertEqual(loaded[0]["_extension_file"], "schema/real_extension.json")

    def test_source_extensions_use_sources_array(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dump_json(
                root / "quests" / "custom.json",
                {"sources": [{"id": "custom-quests", "url": "https://github.com/example/custom-quests"}]},
            )

            loaded = load_domain_extensions(root, "quests")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["id"], "custom-quests")

    def test_local_source_context_key_replaces_repo_source(self) -> None:
        repo_ext = [
            {
                "id": "projecteq-projecteqquests",
                "url": "https://github.com/ProjectEQ/projecteqquests",
                "context_key": "primary-quest-script-examples",
                "_extension_file": "extensions/quests/default_sources.json",
            }
        ]
        local_ext = [
            {
                "id": "custom-quest-scripts",
                "url": "https://github.com/example/custom-quests",
                "context_key": "primary-quest-script-examples",
                "_extension_file": "local-extensions/quests/custom.json",
            }
        ]

        merged = merge_source_records(repo_ext, local_ext, domain="quests")

        self.assertEqual([record["id"] for record in merged], ["custom-quest-scripts"])
        self.assertEqual(merged[0]["provenance"]["effective_source"], "local_extension")

    def test_extension_file_labels_are_stable_for_standard_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_root = root / "extensions"
            dump_json(
                repo_root / "schema" / "real_extension.json",
                {"tables": [{"id": "real_table", "table": "real_table", "mode": "augment"}]},
            )

            loaded = load_domain_extensions(repo_root, "schema")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["_extension_file"], "extensions/schema/real_extension.json")

    def test_find_stale_schema_extensions_detects_upstream_covered_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_root = root / "base"
            repo_root = root / "extensions"
            local_root = root / "local-extensions"

            dump_json(base_root / "quest-api" / "methods.json", [])
            dump_json(base_root / "quest-api" / "events.json", [])
            dump_json(base_root / "quest-api" / "constants.json", [])
            dump_json(
                base_root / "schema" / "index.json",
                [
                    {
                        "id": "tasks",
                        "table": "tasks",
                        "title": "tasks",
                        "category": "content",
                        "columns": [
                            {
                                "name": "allowed_classes",
                                "data_type": "int(10) unsigned",
                                "description": "Already upstream.",
                            }
                        ],
                        "relationships": [],
                    }
                ],
            )
            dump_json(base_root / "docs" / "pages.json", [])
            dump_json(
                repo_root / "schema" / "schema_extension.json",
                {
                    "tables": [
                        {
                            "id": "tasks_allowed_classes",
                            "table": "tasks",
                            "title": "tasks",
                            "category": "custom",
                            "columns": [
                                {
                                    "name": "allowed_classes",
                                    "data_type": "int(10) unsigned",
                                    "description": "Already upstream.",
                                }
                            ],
                            "relationships": [],
                            "mode": "augment",
                        }
                    ]
                },
            )
            (local_root / "schema").mkdir(parents=True, exist_ok=True)

            stale = find_stale_schema_extensions(base_root, repo_root, local_root)

            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0]["id"], "tasks_allowed_classes")
            self.assertEqual(stale[0]["table"], "tasks")
            self.assertIn("already covers", stale[0]["reason"])

    def test_prune_stale_schema_extensions_removes_only_stale_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_root = root / "base"
            repo_root = root / "extensions"
            local_root = root / "local-extensions"

            dump_json(base_root / "quest-api" / "methods.json", [])
            dump_json(base_root / "quest-api" / "events.json", [])
            dump_json(base_root / "quest-api" / "constants.json", [])
            dump_json(
                base_root / "schema" / "index.json",
                [
                    {
                        "id": "tasks",
                        "table": "tasks",
                        "title": "tasks",
                        "category": "content",
                        "columns": [
                            {"name": "allowed_classes", "data_type": "int(10) unsigned", "description": "Already upstream."}
                        ],
                        "relationships": [],
                    }
                ],
            )
            dump_json(base_root / "docs" / "pages.json", [])
            extension_path = repo_root / "schema" / "schema_extension.json"
            dump_json(
                extension_path,
                {
                    "comparison": {"local_ref": "master"},
                    "tables": [
                        {
                            "id": "tasks_allowed_classes",
                            "table": "tasks",
                            "title": "tasks",
                            "columns": [
                                {"name": "allowed_classes", "data_type": "int(10) unsigned", "description": "Already upstream."}
                            ],
                            "relationships": [],
                            "mode": "augment",
                        },
                        {
                            "id": "inventory_item_unique_id",
                            "table": "inventory",
                            "title": "inventory",
                            "columns": [
                                {"name": "item_unique_id", "data_type": "varchar(16)", "description": "Still local only."}
                            ],
                            "relationships": [],
                            "mode": "augment",
                        },
                    ],
                },
            )
            (local_root / "schema").mkdir(parents=True, exist_ok=True)

            result = prune_stale_schema_extensions(base_root, repo_root, local_root, apply=True)

            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["removed_count"], 1)
            payload = load_json(extension_path)
            self.assertEqual(len(payload["tables"]), 1)
            self.assertEqual(payload["tables"][0]["id"], "inventory_item_unique_id")


if __name__ == "__main__":
    unittest.main()
