from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import _search_cache_matches, build_search_index  # noqa: E402
from eqemu_oracle.dataset import DataStore  # noqa: E402
from eqemu_oracle.utils import dump_json  # noqa: E402


class DatasetSearchTest(unittest.TestCase):
    def _manifest_payload(self, quest_count: int) -> dict[str, object]:
        return {
            "counts": {"quest-api": quest_count, "schema": 0, "docs": 0, "docs-sections": 0},
            "merge_scope": "all",
            "sources": {"quest-api": {"source_ref": "abc123"}},
            "extension_health": {"stale_schema_candidate_count": 0, "stale_schema_candidates": []},
        }

    def test_build_search_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dump_json(root / "quest-api" / "records.json", [{"id": "q1", "kind": "method", "language": "perl", "container": "quest", "name": "say", "signature": "say()", "categories": []}])
            dump_json(root / "schema" / "index.json", [{"id": "spawn2", "table": "spawn2", "title": "spawn2", "columns": [{"name": "id", "description": "spawn"}]}])
            dump_json(root / "docs" / "pages.json", [{"id": "server/intro", "path": "server/intro", "slug": "server-intro", "title": "Server Intro", "tags": [], "aliases": []}])
            (root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "pages" / "server-intro.md").write_text("# Server Intro\n\nhello world", encoding="utf-8")
            db_path = root / "search.sqlite3"
            build_search_index(root, db_path)
            self.assertTrue(db_path.exists())

    def test_search_cache_matches_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "merged"
            dump_json(data_root / "quest-api" / "records.json", [{"id": "q1", "kind": "method", "language": "perl", "container": "quest", "name": "say", "signature": "say()", "categories": []}])
            dump_json(data_root / "schema" / "index.json", [])
            dump_json(data_root / "docs" / "pages.json", [])
            dump_json(root / "manifest.json", self._manifest_payload(1))
            (data_root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            db_path = root / "search.sqlite3"
            build_search_index(data_root, db_path)
            self.assertTrue(_search_cache_matches(data_root, db_path))

            dump_json(root / "manifest.json", self._manifest_payload(2))
            self.assertFalse(_search_cache_matches(data_root, db_path))

            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM search_meta")
            conn.commit()
            conn.close()
            self.assertFalse(_search_cache_matches(data_root, db_path))

    def test_datastore_indexes_preserve_exact_lookup_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "merged"
            quest_record = {
                "id": "perl:method:quest:say:test",
                "kind": "method",
                "language": "perl",
                "container": "quest",
                "name": "say",
                "signature": "say(message)",
                "categories": [],
                "related_docs": ["quest-api/methods/quest"],
            }
            dump_json(data_root / "quest-api" / "records.json", [quest_record])
            dump_json(data_root / "quest-api" / "index.json", {"counts": {}, "languages": ["perl"]})
            dump_json(data_root / "schema" / "index.json", [{"id": "spawn2", "table": "spawn2", "title": "spawn2", "columns": []}])
            dump_json(
                data_root / "docs" / "pages.json",
                [
                    {
                        "id": "quest-api/methods/quest",
                        "path": "quest-api/methods/quest",
                        "slug": "quest-api-methods-quest",
                        "title": "Quest Methods",
                        "summary": "Quest method docs",
                        "tags": [],
                        "aliases": [],
                    }
                ],
            )
            (data_root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            (data_root / "docs" / "pages" / "quest-api-methods-quest.md").write_text("# Quest Methods\n\n## say\n\n```perl\nquest::say(\"hello\");\n```", encoding="utf-8")

            with patch("eqemu_oracle.dataset.current_data_root", return_value=data_root):
                with patch("eqemu_oracle.dataset.base_data_root", return_value=data_root):
                    with patch("eqemu_oracle.dataset._current_manifest_path", return_value=None):
                        store = DataStore()

            self.assertEqual(store.get_quest_entry_by_id("perl:method:quest:say:test")["id"], "perl:method:quest:say:test")
            self.assertEqual(store.get_quest_entry("perl", "method", "SAY", "quest")["id"], "perl:method:quest:say:test")
            self.assertEqual(store.get_table("SPAWN2")["table"], "spawn2")
            self.assertEqual(store.get_doc_page("quest-api-methods-quest")["id"], "quest-api/methods/quest")
            self.assertEqual(store.explain_provenance("quest-api", "perl:method:quest:say:test")["id"], "perl:method:quest:say:test")


if __name__ == "__main__":
    unittest.main()
