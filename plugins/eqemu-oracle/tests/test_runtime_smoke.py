from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import DataStore  # noqa: E402


class RuntimeSmokeTest(unittest.TestCase):
    def test_committed_dataset_smoke(self) -> None:
        store = DataStore()
        table = store.get_table("aa_ability")
        self.assertEqual(table["table"], "aa_ability")
        self.assertEqual(table["presentation"]["template"], "schema-entry")
        self.assertIn("```sql", table["presentation"]["markdown"])

        quest = store.get_quest_entry("perl", "method", "Say")
        self.assertEqual(quest["name"], "Say")
        self.assertEqual(quest["presentation"]["template"], "quest-api-entry")
        self.assertIn("```text", quest["presentation"]["markdown"])
        self.assertTrue(quest["presentation"]["copy_blocks"])

        page = store.get_doc_page("quest-api/constants/lua-appearance")
        self.assertEqual(page["title"], "lua-appearance")
        self.assertTrue(page["sections"])
        self.assertEqual(page["presentation"]["template"], "docs-page")

    def test_language_filtered_search(self) -> None:
        store = DataStore()
        perl_result = store.search("getx", ["quest-api"], 20, True, "perl")
        lua_result = store.search("getx", ["quest-api"], 20, True, "lua")
        perl_hits = perl_result["hits"]
        lua_hits = lua_result["hits"]
        self.assertTrue(perl_hits)
        self.assertTrue(lua_hits)
        self.assertTrue(all(hit["id"].startswith("perl:") for hit in perl_hits))
        self.assertTrue(all(hit["id"].startswith("lua:") for hit in lua_hits))
        self.assertEqual(perl_result["presentation"]["template"], "search-results")

    def test_docs_section_search(self) -> None:
        store = DataStore()
        hits = store.search("multi quest npc support", ["docs"], 5, True)["hits"]
        self.assertTrue(hits)
        self.assertIn("quest-api/npc-item-handin", hits[0]["uri"])
        self.assertIn(hits[0]["entity_type"], {"page", "section"})


if __name__ == "__main__":
    unittest.main()
