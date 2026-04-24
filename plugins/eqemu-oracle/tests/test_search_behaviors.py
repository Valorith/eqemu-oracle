from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import DataStore, load_json, write_merged_dataset  # noqa: E402
from eqemu_oracle.utils import dump_json  # noqa: E402


class SearchBehaviorTest(unittest.TestCase):
    def _manifest_payload(self, quest_count: int) -> dict[str, object]:
        return {
            "counts": {"quest-api": quest_count, "schema": 0, "docs": 0, "docs-sections": 0},
            "merge_scope": "all",
            "sources": {"quest-api": {"source_ref": "abc123"}},
            "extension_health": {"stale_schema_candidate_count": 0, "stale_schema_candidates": []},
        }

    def test_prefer_fresh_promotes_newer_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dump_json(root / "quest-api" / "records.json", [])
            dump_json(root / "quest-api" / "index.json", {"counts": {}, "languages": []})
            dump_json(root / "schema" / "index.json", [])
            dump_json(
                root / "docs" / "pages.json",
                [
                    {
                        "id": "docs/old",
                        "path": "docs/old",
                        "slug": "docs-old",
                        "title": "Spawn Guide Alpha",
                        "summary": "spawn guide reference",
                        "fetched_at": "2024-01-01T00:00:00Z",
                        "tags": [],
                        "aliases": [],
                    },
                    {
                        "id": "docs/new",
                        "path": "docs/new",
                        "slug": "docs-new",
                        "title": "Spawn Guide Zeta",
                        "summary": "spawn guide reference",
                        "fetched_at": "2026-01-01T00:00:00Z",
                        "tags": [],
                        "aliases": [],
                    },
                ],
            )
            (root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "pages" / "docs-old.md").write_text("# Spawn Guide Alpha\n\nspawn guide reference", encoding="utf-8")
            (root / "docs" / "pages" / "docs-new.md").write_text("# Spawn Guide Zeta\n\nspawn guide reference", encoding="utf-8")

            with patch("eqemu_oracle.dataset.current_data_root", return_value=root):
                with patch("eqemu_oracle.dataset.base_data_root", return_value=root):
                    with patch("eqemu_oracle.dataset._current_manifest_path", return_value=None):
                        with patch("eqemu_oracle.dataset.SEARCH_DB_PATH", root / "search.sqlite3"):
                            store = DataStore()
                            default_hits = store.search("spawn guide", ["docs"], 2, True, None, False)["hits"]
                            fresh_hits = store.search("spawn guide", ["docs"], 2, True, None, True)["hits"]

            self.assertTrue(default_hits[0]["id"].startswith("docs/old"))
            self.assertTrue(fresh_hits[0]["id"].startswith("docs/new"))
            self.assertGreater(fresh_hits[0]["freshness_ts"], default_hits[0]["freshness_ts"])

    def test_scoped_merge_preserves_unrelated_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_root = root / "base"
            target_root = root / "merged"
            search_db = root / "search.sqlite3"

            dump_json(
                base_root / "quest-api" / "methods.json",
                [
                    {
                        "id": "perl:method:mob:say:test",
                        "language": "perl",
                        "kind": "method",
                        "container": "mob",
                        "name": "Say",
                        "signature": "Say(text)",
                        "categories": [],
                    }
                ],
            )
            dump_json(base_root / "quest-api" / "events.json", [])
            dump_json(base_root / "quest-api" / "constants.json", [])
            dump_json(base_root / "schema" / "index.json", [])
            dump_json(base_root / "docs" / "pages.json", [])

            dump_json(target_root / "quest-api" / "records.json", [])
            dump_json(target_root / "quest-api" / "index.json", {"counts": {}, "languages": []})
            dump_json(target_root / "schema" / "index.json", [{"id": "existing_schema", "table": "existing_schema", "title": "existing_schema", "columns": []}])
            dump_json(target_root / "docs" / "pages.json", [{"id": "existing/doc", "path": "existing/doc", "slug": "existing-doc", "title": "Existing Doc"}])
            dump_json(target_root / "docs" / "sections.json", [])
            (target_root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            (target_root / "docs" / "pages" / "existing-doc.md").write_text("# Existing Doc", encoding="utf-8")

            with patch("eqemu_oracle.dataset.validate_extension_overlays"):
                with patch("eqemu_oracle.dataset.find_stale_schema_extensions", return_value=[]):
                    with patch("eqemu_oracle.dataset.load_domain_extensions", return_value=[]):
                        with patch("eqemu_oracle.dataset.SEARCH_DB_PATH", search_db):
                            manifest = write_merged_dataset(base_root, target_root, scope="quest-api")

            self.assertEqual(manifest["merge_scope"], "all")
            quest_records = load_json(target_root / "quest-api" / "records.json")
            schema_records = load_json(target_root / "schema" / "index.json")
            docs_records = load_json(target_root / "docs" / "pages.json")
            self.assertEqual(quest_records[0]["name"], "Say")
            self.assertEqual(schema_records[0]["id"], "existing_schema")
            self.assertEqual(docs_records[0]["id"], "existing/doc")

    def test_search_rebuilds_cache_when_manifest_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "merged"
            dump_json(data_root / "quest-api" / "records.json", [])
            dump_json(data_root / "quest-api" / "index.json", {"counts": {}, "languages": []})
            dump_json(data_root / "schema" / "index.json", [])
            dump_json(data_root / "docs" / "pages.json", [])
            dump_json(root / "manifest.json", self._manifest_payload(0))
            (data_root / "docs" / "pages").mkdir(parents=True, exist_ok=True)

            with patch("eqemu_oracle.dataset.current_data_root", return_value=data_root):
                with patch("eqemu_oracle.dataset.base_data_root", return_value=data_root):
                    with patch("eqemu_oracle.dataset._current_manifest_path", return_value=root / "manifest.json"):
                        with patch("eqemu_oracle.dataset.SEARCH_DB_PATH", root / "search.sqlite3"):
                            store = DataStore()
                            store.search("anything", ["docs"], 1, True)
                            dump_json(root / "manifest.json", self._manifest_payload(1))
                            with patch("eqemu_oracle.dataset.build_search_index") as build_search_index:
                                build_search_index.side_effect = lambda data_root, db_path: None
                                store.search("anything", ["docs"], 1, True)

            build_search_index.assert_called_once()

    def test_search_event_say_returns_perl_events_at_normal_limit(self) -> None:
        store = DataStore()
        hits = store.search("EVENT_SAY", ["quest-api"], 5, True, "perl", False)["hits"]

        self.assertTrue(hits)
        self.assertTrue(all(hit["id"].startswith("perl:") for hit in hits))
        self.assertTrue(any(hit["entity_type"] == "event" and "event-say" in hit["id"] for hit in hits))

    def test_unscoped_search_finds_exact_schema_table(self) -> None:
        store = DataStore()
        hits = store.search("npc_types", None, 5, True, None, False)["hits"]

        self.assertIn("npc_types", {hit["id"] for hit in hits})

    def test_quest_say_search_prefers_api_methods_over_constants(self) -> None:
        store = DataStore()
        hits = store.search("quest::say", None, 5, True, None, False)["hits"]

        self.assertTrue(hits)
        self.assertEqual(hits[0]["domain"], "quest-api")
        self.assertEqual(hits[0]["entity_type"], "method")
        self.assertIn(":method:quest:say:", hits[0]["id"])

    def test_search_fallback_scans_all_selected_domain_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "merged"
            dump_json(data_root / "quest-api" / "records.json", [])
            dump_json(data_root / "quest-api" / "index.json", {"counts": {}, "languages": []})
            dump_json(
                data_root / "schema" / "index.json",
                [
                    {"id": f"filler_{index}", "table": f"filler_{index}", "title": f"filler_{index}", "columns": []}
                    for index in range(1005)
                ]
                + [{"id": "needle_table", "table": "needle_table", "title": "needle_table", "columns": []}],
            )
            dump_json(data_root / "docs" / "pages.json", [])
            dump_json(root / "manifest.json", self._manifest_payload(0))
            (data_root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            search_db = root / "search.sqlite3"

            with patch("eqemu_oracle.dataset.current_data_root", return_value=data_root):
                with patch("eqemu_oracle.dataset.base_data_root", return_value=data_root):
                    with patch("eqemu_oracle.dataset._current_manifest_path", return_value=root / "manifest.json"):
                        with patch("eqemu_oracle.dataset.SEARCH_DB_PATH", search_db):
                            store = DataStore()
                            store.search("filler", ["schema"], 1, True)
                            conn = sqlite3.connect(search_db)
                            conn.execute("DROP TABLE search_fts")
                            conn.commit()
                            conn.close()

                            hits = store.search("needle_table", ["schema"], 1, True)["hits"]

            self.assertEqual([hit["id"] for hit in hits], ["needle_table"])


if __name__ == "__main__":
    unittest.main()
