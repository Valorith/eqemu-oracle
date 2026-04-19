from __future__ import annotations

import json
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
            get_quest_entry_by_id=lambda *args, **kwargs: {"id": "perl:method:mob:say:test", "name": "Say"},
            summarize_quest_topic=lambda *args, **kwargs: {},
            get_table=lambda *args, **kwargs: {"table": "aa_ability", "presentation": {"markdown": "schema markdown"}},
            get_doc_page=lambda *args, **kwargs: {},
            explain_provenance=lambda *args, **kwargs: {"domain": "schema", "id": "aa_ability", "source_url": "https://example.test"},
            manifest=lambda: {},
            quest_index=lambda: {},
            schema_index=lambda: [],
            docs_index=lambda: [],
            docs_sections=[{"id": "page#section", "page_id": "page", "heading": "Section"}],
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

        with patch("eqemu_oracle.mcp.prune_schema_extensions_dataset", return_value=(prune_result, {"merge_scope": "schema"})) as prune_mock:
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
        prune_mock.assert_called_once_with(apply=True, mode="committed")
        self.assertEqual(response["result"]["structuredContent"]["removed_count"], 1)

    def test_rebuild_extensions_tool_forwards_scope(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        with patch("eqemu_oracle.mcp.rebuild_extensions_dataset", return_value={"merge_scope": "docs"}) as rebuild_mock:
            with patch("eqemu_oracle.mcp.DataStore", return_value=server.store):
                with patch.object(server, "_schema_extension_advisories", return_value={"stale_schema_candidate_count": 0, "stale_schema_candidates": []}):
                    response = server.handle(
                        {
                            "jsonrpc": "2.0",
                            "id": 4,
                            "method": "tools/call",
                            "params": {"name": "rebuild_eqemu_extensions", "arguments": {"scope": "docs"}},
                        }
                    )

        self.assertIsNotNone(response)
        assert response is not None
        rebuild_mock.assert_called_once_with(scope="docs", mode="committed")
        self.assertEqual(response["result"]["structuredContent"]["merge_scope"], "docs")

    def test_refresh_tool_uses_shared_operation(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        with patch("eqemu_oracle.mcp.refresh_dataset", return_value={"merge_scope": "schema"}) as refresh_mock:
            with patch("eqemu_oracle.mcp.DataStore", return_value=server.store):
                with patch.object(server, "_schema_extension_advisories", return_value={"stale_schema_candidate_count": 0, "stale_schema_candidates": []}):
                    response = server.handle(
                        {
                            "jsonrpc": "2.0",
                            "id": 4,
                            "method": "tools/call",
                            "params": {"name": "refresh_eqemu_oracle", "arguments": {"scope": "schema", "mode": "committed"}},
                        }
                    )

        self.assertIsNotNone(response)
        assert response is not None
        refresh_mock.assert_called_once_with(scope="schema", mode="committed")
        self.assertEqual(response["result"]["structuredContent"]["merge_scope"], "schema")

    def test_quest_api_resource_returns_entry_payload(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        with patch.object(server, "_preflight_extensions") as preflight:
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "resources/read",
                    "params": {"uri": "eqemu://quest-api/perl:method:mob:say:test"},
                }
            )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(preflight.call_count, 1)
        text = response["result"]["contents"][0]["text"]
        self.assertIn('"id": "perl:method:mob:say:test"', text)
        self.assertIn('"name": "Say"', text)
        self.assertNotIn('"domain": "quest-api"', text)

    def test_resources_list_exposes_indexes_and_templates(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        resource_response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "resources/list",
                "params": {},
            }
        )
        template_response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "resources/templates/list",
                "params": {},
            }
        )

        self.assertIsNotNone(resource_response)
        self.assertIsNotNone(template_response)
        assert resource_response is not None
        assert template_response is not None
        resources = resource_response["result"]["resources"]
        templates = template_response["result"]["resourceTemplates"]
        self.assertIn("eqemu://indexes/docs-sections", {item["uri"] for item in resources})
        self.assertIn("eqemu://provenance/{domain}/{id}", {item["uriTemplate"] for item in templates})

    def test_provenance_resource_returns_payload(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        with patch.object(server, "_preflight_extensions") as preflight:
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "resources/read",
                    "params": {"uri": "eqemu://provenance/schema/aa_ability"},
                }
            )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(preflight.call_count, 1)
        text = response["result"]["contents"][0]["text"]
        self.assertIn('"domain": "schema"', text)
        self.assertIn('"id": "aa_ability"', text)

    def test_invalid_search_limit_returns_error(self) -> None:
        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "search_eqemu_context", "arguments": {"query": "say", "limit": 0}},
            }
        )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("integer >= 1", response["error"]["message"])

    def test_initialize_reports_plugin_manifest_version(self) -> None:
        plugin_manifest_path = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
        plugin_manifest = json.loads(plugin_manifest_path.read_text(encoding="utf-8"))

        with patch("eqemu_oracle.mcp.DataStore", return_value=self._stub_store()):
            server = McpServer()

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
            }
        )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["result"]["serverInfo"]["version"], plugin_manifest["version"])


if __name__ == "__main__":
    unittest.main()
