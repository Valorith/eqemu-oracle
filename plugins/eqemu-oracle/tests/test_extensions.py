from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.extensions import merge_records  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
