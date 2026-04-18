from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.dataset import DataStore  # noqa: E402


class RuntimeSmokeTest(unittest.TestCase):
    def test_committed_dataset_smoke(self) -> None:
        store = DataStore()
        self.assertEqual(store.get_table("aa_ability")["table"], "aa_ability")
        self.assertEqual(store.get_quest_entry("perl", "method", "Say")["name"], "Say")
        self.assertEqual(store.get_doc_page("quest-api/constants/lua-appearance")["title"], "lua-appearance")

    def test_language_filtered_search(self) -> None:
        store = DataStore()
        perl_hits = store.search("getx", ["quest-api"], 20, True, "perl")["hits"]
        lua_hits = store.search("getx", ["quest-api"], 20, True, "lua")["hits"]
        self.assertTrue(perl_hits)
        self.assertTrue(lua_hits)
        self.assertTrue(all(hit["id"].startswith("perl:") for hit in perl_hits))
        self.assertTrue(all(hit["id"].startswith("lua:") for hit in lua_hits))


if __name__ == "__main__":
    unittest.main()
