from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .constants import OVERLAY_ROOT, PLUGIN_VERSION, SERVER_NAME
from .dataset import DataStore, write_merged_dataset
from .ingest import write_base_dataset
from .updater import update_plugin_repo


class McpServer:
    def __init__(self) -> None:
        self.store = DataStore()

    def _content_text(self, result: Any) -> str:
        if isinstance(result, dict):
            presentation = result.get("presentation")
            if isinstance(presentation, dict) and isinstance(presentation.get("markdown"), str):
                return presentation["markdown"]
        return json.dumps(result, indent=2, sort_keys=True, default=str)

    def _reply(self, request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        return payload

    def _read_message(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode("utf-8").partition(":")
            headers[name.lower()] = value.strip()
        body = sys.stdin.buffer.read(int(headers.get("content-length", "0")))
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def _write_message(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    def _tool_spec(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_eqemu_context",
                "description": "Search staged EQEmu quest API, schema, and docs context.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "domains": {"type": "array", "items": {"type": "string"}},
                        "language": {"type": "string"},
                        "limit": {"type": "integer"},
                        "include_extensions": {"type": "boolean"},
                        "prefer_fresh": {"type": "boolean"}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "get_quest_api_entry",
                "description": "Get an exact quest API method, event, or constant entry.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string"},
                        "kind": {"type": "string"},
                        "name": {"type": "string"},
                        "group_or_type": {"type": "string"}
                    },
                    "required": ["language", "kind", "name"]
                }
            },
            {
                "name": "get_db_table",
                "description": "Get an exact EQEmu schema table entry.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"table_name": {"type": "string"}},
                    "required": ["table_name"]
                }
            },
            {
                "name": "get_doc_page",
                "description": "Get an exact official docs page.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path_or_slug": {"type": "string"}},
                    "required": ["path_or_slug"]
                }
            },
            {
                "name": "explain_eqemu_provenance",
                "description": "Explain where a merged record came from and which overlays affected it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "id": {"type": "string"}
                    },
                    "required": ["domain", "id"]
                }
            },
            {
                "name": "refresh_eqemu_oracle",
                "description": "Refresh upstream data into an overlay workspace and rebuild merged data.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string"},
                        "mode": {"type": "string"}
                    }
                }
            },
            {
                "name": "rebuild_eqemu_extensions",
                "description": "Rebuild merged data and the search cache from existing base data plus extensions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"scope": {"type": "string"}}
                }
            },
            {
                "name": "update_eqemu_oracle_plugin",
                "description": "Pull the EQEmu Oracle plugin repo from Git and rebuild committed merged data.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "remote": {"type": "string"},
                        "branch": {"type": "string"},
                        "allow_dirty": {"type": "boolean"},
                        "skip_rebuild": {"type": "boolean"}
                    }
                }
            }
        ]

    def _resources(self) -> list[dict[str, Any]]:
        return [
            {"uri": "eqemu://manifest", "name": "Manifest", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/quest-api", "name": "Quest API Index", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/schema", "name": "Schema Index", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/docs", "name": "Docs Index", "mimeType": "application/json"},
        ]

    def _resource_templates(self) -> list[dict[str, Any]]:
        return [
            {"uriTemplate": "eqemu://quest-api/{id}", "name": "Quest API Entry", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://schema/table/{table_name}", "name": "Schema Table", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://docs/page/{path}", "name": "Docs Page", "mimeType": "application/json"},
        ]

    def _handle_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_eqemu_context":
            result = self.store.search(
                arguments["query"],
                arguments.get("domains"),
                int(arguments.get("limit", 10)),
                bool(arguments.get("include_extensions", True)),
                arguments.get("language"),
                bool(arguments.get("prefer_fresh", False)),
            )
        elif name == "get_quest_api_entry":
            result = self.store.get_quest_entry(arguments["language"], arguments["kind"], arguments["name"], arguments.get("group_or_type"))
        elif name == "get_db_table":
            result = self.store.get_table(arguments["table_name"])
        elif name == "get_doc_page":
            result = self.store.get_doc_page(arguments["path_or_slug"])
        elif name == "explain_eqemu_provenance":
            result = self.store.explain_provenance(arguments["domain"], arguments["id"])
        elif name == "refresh_eqemu_oracle":
            scope = arguments.get("scope", "all")
            mode = arguments.get("mode", "overlay")
            if mode == "committed":
                from .constants import BASE_ROOT, MERGED_ROOT

                write_base_dataset(BASE_ROOT, scope=scope)
                result = write_merged_dataset(BASE_ROOT, MERGED_ROOT)
            else:
                write_base_dataset(OVERLAY_ROOT / "base", scope=scope)
                result = write_merged_dataset(OVERLAY_ROOT / "base", OVERLAY_ROOT / "merged")
            self.store = DataStore()
        elif name == "rebuild_eqemu_extensions":
            result = write_merged_dataset(self.store.base_root, self.store.data_root)
            self.store = DataStore()
        elif name == "update_eqemu_oracle_plugin":
            result = update_plugin_repo(
                remote=arguments.get("remote", "origin"),
                branch=arguments.get("branch"),
                allow_dirty=bool(arguments.get("allow_dirty", False)),
                skip_rebuild=bool(arguments.get("skip_rebuild", False)),
            )
            self.store = DataStore()
        else:
            raise ValueError(f"Unknown tool '{name}'")
        return {
            "content": [{"type": "text", "text": self._content_text(result)}],
            "structuredContent": result,
            "isError": result is None,
        }

    def _read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "eqemu://manifest":
            payload = self.store.manifest()
        elif uri == "eqemu://indexes/quest-api":
            payload = self.store.quest_index()
        elif uri == "eqemu://indexes/schema":
            payload = self.store.schema_index()
        elif uri == "eqemu://indexes/docs":
            payload = self.store.docs_index()
        elif uri.startswith("eqemu://quest-api/"):
            payload = self.store.explain_provenance("quest-api", uri.removeprefix("eqemu://quest-api/"))
        elif uri.startswith("eqemu://schema/table/"):
            payload = self.store.get_table(uri.removeprefix("eqemu://schema/table/"))
        elif uri.startswith("eqemu://docs/page/"):
            payload = self.store.get_doc_page(uri.removeprefix("eqemu://docs/page/"))
        else:
            raise ValueError(f"Unknown resource '{uri}'")
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}]}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        params = message.get("params", {})
        request_id = message.get("id")
        if method == "initialize":
            return self._reply(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": PLUGIN_VERSION},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._reply(request_id, {})
        if method == "tools/list":
            return self._reply(request_id, {"tools": self._tool_spec()})
        if method == "tools/call":
            try:
                return self._reply(request_id, self._handle_tool(params["name"], params.get("arguments", {})))
            except Exception as exc:  # pragma: no cover
                return self._reply(request_id, error={"code": -32000, "message": str(exc)})
        if method == "resources/list":
            return self._reply(request_id, {"resources": self._resources()})
        if method == "resources/templates/list":
            return self._reply(request_id, {"resourceTemplates": self._resource_templates()})
        if method == "resources/read":
            try:
                return self._reply(request_id, self._read_resource(params["uri"]))
            except Exception as exc:  # pragma: no cover
                return self._reply(request_id, error={"code": -32000, "message": str(exc)})
        return self._reply(request_id, error={"code": -32601, "message": f"Method not found: {method}"})

    def serve(self) -> int:
        while True:
            message = self._read_message()
            if message is None:
                return 0
            response = self.handle(message)
            if response is not None:
                self._write_message(response)


def serve_mcp(_args: argparse.Namespace) -> int:
    return McpServer().serve()
