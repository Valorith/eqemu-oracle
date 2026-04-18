from __future__ import annotations

import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle.extensions import ExtensionValidationError  # noqa: E402
from eqemu_oracle.mcp import McpServer  # noqa: E402


class McpServerValidationTest(unittest.TestCase):
    def _stub_store(self) -> SimpleNamespace:
        return SimpleNamespace(
            base_root=Path("C:/base"),
            data_root=Path("C:/merged"),
            search=lambda *args, **kwargs: {},
            get_quest_entry=lambda *args, **kwargs: {},
            summarize_quest_topic=lambda *args, **kwargs: {},
            get_table=lambda *args, **kwargs: {"table": "aa_ability", "presentation": {"markdown": "schema markdown"}},
            get_doc_page=lambda *args, **kwargs: {},
            explain_provenance=lambda *args, **kwargs: {},
            manifest=lambda: {},
            quest_index=lambda: {},
            schema_index=lambda: [],
            docs_index=lambda: [],
        )

    def test_tool_call_preflights_extensions_and_returns_feedback(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()
        error = ExtensionValidationError(["schema: broken extension"])

        with patch.object(server, "_preflight_extensions", side_effect=error) as preflight:
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "get_db_table", "arguments": {"table_name": "aa_ability"}},
                }
            )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(preflight.call_count, 1)
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("extension validation failed", response["error"]["message"].lower())
        self.assertIn("schema: broken extension", response["error"]["message"])

    def test_tool_call_includes_stale_schema_extension_warning(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()
        advisory = {
            "stale_schema_candidate_count": 1,
            "stale_schema_candidates": [
                {
                    "id": "tasks_allowed_classes",
                    "table": "tasks",
                    "file": "schema_extension.json",
                }
            ],
            "prune_tool": {
                "name": "prune_stale_schema_extensions",
                "preview_arguments": {"apply": False},
                "apply_arguments": {"apply": True},
            },
        }

        with patch.object(server, "_preflight_extensions") as preflight:
            with patch.object(server, "_schema_extension_advisories", return_value=advisory):
                response = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "get_db_table", "arguments": {"table_name": "aa_ability"}},
                    }
                )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(preflight.call_count, 1)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["schema_extension_health"]["stale_schema_candidate_count"], 1)
        self.assertIn("Schema Extension Advisory", response["result"]["content"][0]["text"])
        self.assertIn("prune_stale_schema_extensions", response["result"]["content"][0]["text"])

    def test_prune_stale_schema_extensions_tool_can_apply_changes(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()
        prune_result = {"apply": True, "candidate_count": 1, "removed_count": 1, "stale_candidates": [], "removed_entries": [], "files": []}

        with patch("eqemu_oracle.mcp.prune_stale_schema_extensions", return_value=prune_result) as prune_mock:
            with patch("eqemu_oracle.mcp.write_merged_dataset") as rebuild_mock:
                with patch("eqemu_oracle.mcp.DataStore", return_value=server.store):
                    with patch.object(server, "_schema_extension_advisories", return_value={"stale_schema_candidate_count": 0, "stale_schema_candidates": []}):
                        response = server.handle(
                            {
                                "jsonrpc": "2.0",
                                "id": 3,
                                "method": "tools/call",
                                "params": {"name": "prune_stale_schema_extensions", "arguments": {"apply": True}},
                            }
                        )

        self.assertIsNotNone(response)
        assert response is not None
        prune_mock.assert_called_once()
        rebuild_mock.assert_called_once()
        self.assertEqual(response["result"]["structuredContent"]["removed_count"], 1)


if __name__ == "__main__":
    unittest.main()
