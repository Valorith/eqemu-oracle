from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.config import SourceConfigError, load_source_config  # noqa: E402


class SourceConfigTest(unittest.TestCase):
    def test_load_source_config_requires_toml_parser_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "sources.toml"
            default_path.write_text("[quest_api]\ndefinitions_url = 'https://spire.example/api'\nrepo = 'https://github.com/example/spire'\nbranch = 'main'\n", encoding="utf-8")

            with unittest.mock.patch("eqemu_oracle.config._tomllib", None):
                with self.assertRaises(SourceConfigError):
                    load_source_config(default_path, root / "missing.local.toml")

    def test_load_source_config_applies_local_override_and_derives_github_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "sources.toml"
            override_path = root / "sources.local.toml"
            default_path.write_text(
                "\n".join(
                    [
                        "[quest_api]",
                        'definitions_url = "https://spire.example/api"',
                        'repo = "https://github.com/example/spire"',
                        'branch = "main"',
                        "",
                        "[docs]",
                        'repo = "https://github.com/example/docs"',
                        'branch = "stable"',
                        'site_base_url = "https://docs.example"',
                    ]
                ),
                encoding="utf-8",
            )
            override_path.write_text(
                "\n".join(
                    [
                        "[quest_api]",
                        'branch = "dev"',
                        "",
                        "[docs]",
                        'branch = "feature/docs"',
                    ]
                ),
                encoding="utf-8",
            )

            config = load_source_config(default_path, override_path)

        self.assertEqual(config["quest_api"]["branch"], "dev")
        self.assertEqual(config["quest_api"]["commit_api"], "https://api.github.com/repos/example/spire/commits/dev")
        self.assertEqual(config["docs"]["archive_url"], "https://github.com/example/docs/archive/refs/heads/feature/docs.zip")
        self.assertEqual(config["docs"]["source_file_base"], "https://github.com/example/docs/blob/feature/docs")

    def test_load_source_config_requires_manual_endpoints_for_non_github_docs_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "sources.toml"
            default_path.write_text(
                "\n".join(
                    [
                        "[quest_api]",
                        'definitions_url = "https://spire.example/api"',
                        'repo = "https://github.com/example/spire"',
                        'branch = "main"',
                        "",
                        "[docs]",
                        'repo = "https://git.example.com/eqemu/docs"',
                        'branch = "main"',
                        'site_base_url = "https://docs.example"',
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SourceConfigError):
                load_source_config(default_path, root / "missing.local.toml")


if __name__ == "__main__":
    unittest.main()
