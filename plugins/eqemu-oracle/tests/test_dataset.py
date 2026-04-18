from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import _search_cache_matches, build_search_index  # noqa: E402
from eqemu_oracle.utils import dump_json  # noqa: E402


class DatasetSearchTest(unittest.TestCase):
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
            dump_json(root / "manifest.json", {"snapshot_version": "20260101010101", "generated_at": "2026-01-01T01:01:01Z"})
            (data_root / "docs" / "pages").mkdir(parents=True, exist_ok=True)
            db_path = root / "search.sqlite3"
            build_search_index(data_root, db_path)
            self.assertTrue(_search_cache_matches(data_root, db_path))

            dump_json(root / "manifest.json", {"snapshot_version": "20260202020202", "generated_at": "2026-02-02T02:02:02Z"})
            self.assertFalse(_search_cache_matches(data_root, db_path))

            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM search_meta")
            conn.commit()
            conn.close()
            self.assertFalse(_search_cache_matches(data_root, db_path))


if __name__ == "__main__":
    unittest.main()
