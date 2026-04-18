from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .constants import EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT, OVERLAY_ROOT, PLUGIN_VERSION, SERVER_NAME
from .dataset import DataStore, base_data_root, find_stale_schema_extensions, prune_stale_schema_extensions, validate_extension_overlays, write_merged_dataset
from .extensions import ExtensionValidationError, extension_inputs_fingerprint
from .ingest import write_base_dataset
from .updater import update_plugin_repo


class McpServer:
    def __init__(self) -> None:
        self.store = DataStore()
        self._extension_validation_fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._extension_validation_error: ExtensionValidationError | None = None
        self._schema_extension_health_fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._schema_extension_health: dict[str, Any] | None = None

    def _preflight_extensions(self) -> None:
        fingerprint = extension_inputs_fingerprint(EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT)
        if fingerprint == self._extension_validation_fingerprint:
            if self._extension_validation_error is not None:
                raise self._extension_validation_error
            return
        base_root = getattr(self.store, "base_root", base_data_root())
        try:
            validate_extension_overlays(base_root)
        except ExtensionValidationError as exc:
            self._extension_validation_fingerprint = fingerprint
            self._extension_validation_error = exc
            raise
        self._extension_validation_fingerprint = fingerprint
        self._extension_validation_error = None

    def _reset_extension_validation(self) -> None:
        self._extension_validation_fingerprint = None
        self._extension_validation_error = None
        self._schema_extension_health_fingerprint = None
        self._schema_extension_health = None

    def _schema_extension_advisories(self) -> dict[str, Any]:
        fingerprint = extension_inputs_fingerprint(EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT)
        if fingerprint == self._schema_extension_health_fingerprint and self._schema_extension_health is not None:
            return self._schema_extension_health
        base_root = getattr(self.store, "base_root", base_data_root())
        stale_candidates = find_stale_schema_extensions(base_root)
        advisory = {
            "stale_schema_candidate_count": len(stale_candidates),
            "stale_schema_candidates": stale_candidates,
            "prune_tool": {
                "name": "prune_stale_schema_extensions",
                "preview_arguments": {"apply": False},
                "apply_arguments": {"apply": True},
            },
        }
        self._schema_extension_health_fingerprint = fingerprint
        self._schema_extension_health = advisory
        return advisory

    def _add_schema_extension_advisories(self, result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        advisory = self._schema_extension_advisories()
        if advisory.get("stale_schema_candidate_count", 0) <= 0:
            return result
        enriched = dict(result)
        enriched["schema_extension_health"] = advisory
        return enriched

    def _content_text(self, result: Any) -> str:
        if isinstance(result, dict):
            presentation = result.get("presentation")
            base_text = presentation["markdown"] if isinstance(presentation, dict) and isinstance(presentation.get("markdown"), str) else json.dumps(result, indent=2, sort_keys=True, default=str)
            advisory = result.get("schema_extension_health")
            if not isinstance(advisory, dict) or int(advisory.get("stale_schema_candidate_count", 0)) <= 0:
                return base_text
            candidates = advisory.get("stale_schema_candidates", [])
            lines = [
                base_text,
                "",
                "## Schema Extension Advisory",
                "",
                (
                    f"{advisory['stale_schema_candidate_count']} schema extension entr"
                    f"{'y looks' if advisory['stale_schema_candidate_count'] == 1 else 'ies look'} stale because upstream schema already covers the same payload."
                ),
                "Run `prune_stale_schema_extensions` to review them or call it with `apply: true` to remove them automatically.",
            ]
            for candidate in candidates[:5]:
                lines.append(
                    f"- `{candidate.get('id')}` for table `{candidate.get('table')}` in `{candidate.get('file')}`"
                )
            if len(candidates) > 5:
                lines.append(f"- ...and {len(candidates) - 5} more")
            return "\n".join(lines)
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
                "name": "summarize_quest_api_topic",
                "description": "Summarize a broad quest scripting topic with scripting-oriented examples and grouped APIs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "language": {"type": "string"},
                        "limit": {"type": "integer"}
                    },
                    "required": ["query"]
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
                "name": "prune_stale_schema_extensions",
                "description": "Preview or remove schema extension entries that appear stale because upstream schema already covers them.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "apply": {"type": "boolean"}
                    }
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
        if name in {
            "search_eqemu_context",
            "get_quest_api_entry",
            "summarize_quest_api_topic",
            "get_db_table",
            "get_doc_page",
            "explain_eqemu_provenance",
        }:
            self._preflight_extensions()
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
        elif name == "summarize_quest_api_topic":
            result = self.store.summarize_quest_topic(arguments["query"], arguments.get("language", "perl"), int(arguments.get("limit", 16)))
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
            self._reset_extension_validation()
        elif name == "rebuild_eqemu_extensions":
            result = write_merged_dataset(self.store.base_root, self.store.data_root)
            self.store = DataStore()
            self._reset_extension_validation()
        elif name == "prune_stale_schema_extensions":
            result = prune_stale_schema_extensions(self.store.base_root, apply=bool(arguments.get("apply", False)))
            if arguments.get("apply", False) and result.get("removed_count"):
                write_merged_dataset(self.store.base_root, self.store.data_root)
                self.store = DataStore()
            self._reset_extension_validation()
        elif name == "update_eqemu_oracle_plugin":
            result = update_plugin_repo(
                remote=arguments.get("remote", "origin"),
                branch=arguments.get("branch"),
                allow_dirty=bool(arguments.get("allow_dirty", False)),
                skip_rebuild=bool(arguments.get("skip_rebuild", False)),
            )
            self.store = DataStore()
            self._reset_extension_validation()
        else:
            raise ValueError(f"Unknown tool '{name}'")
        if name in {
            "search_eqemu_context",
            "get_quest_api_entry",
            "summarize_quest_api_topic",
            "get_db_table",
            "get_doc_page",
            "explain_eqemu_provenance",
            "rebuild_eqemu_extensions",
            "prune_stale_schema_extensions",
            "update_eqemu_oracle_plugin",
            "refresh_eqemu_oracle",
        }:
            result = self._add_schema_extension_advisories(result)
        return {
            "content": [{"type": "text", "text": self._content_text(result)}],
            "structuredContent": result,
            "isError": result is None,
        }

    def _read_resource(self, uri: str) -> dict[str, Any]:
        self._preflight_extensions()
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
