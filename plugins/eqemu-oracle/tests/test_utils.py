from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.utils import markdown_sections, split_identifier_words  # noqa: E402


class UtilsTest(unittest.TestCase):
    def test_split_identifier_words_handles_mixed_identifiers(self) -> None:
        self.assertEqual(split_identifier_words("MySQLPreparedStmt"), ["my", "sql", "prepared", "stmt"])
        self.assertEqual(split_identifier_words("aa_ability"), ["aa", "ability"])

    def test_markdown_sections_splits_headings_and_preserves_overview(self) -> None:
        markdown = "# Title\n\nIntro paragraph.\n\n## First Section\n\nDetails.\n\n## First Section\n\nMore details."
        sections = markdown_sections(markdown)
        self.assertEqual(sections[0]["title"], "Title")
        self.assertEqual(sections[1]["title"], "First Section")
        self.assertEqual(sections[1]["anchor"], "first-section")
        self.assertEqual(sections[2]["anchor"], "first-section-2")


if __name__ == "__main__":
    unittest.main()
