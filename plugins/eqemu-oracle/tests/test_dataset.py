from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import build_search_index  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
