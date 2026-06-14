from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .constants import (
    DOMAIN_CHOICES,
    EXTENSIONS_ROOT,
    LOCAL_EXTENSIONS_ROOT,
    MODE_CHOICES,
    PLUGIN_VERSION,
    QUEST_KIND_CHOICES,
    QUEST_LANGUAGE_CHOICES,
    SCOPE_CHOICES,
    SERVER_NAME,
)
from .dataset import DataStore, base_data_root, find_stale_schema_extensions, validate_extension_overlays
from .extensions import ExtensionValidationError, extension_inputs_digest, extension_inputs_fingerprint, load_domain_extensions
from .operations import prune_schema_extensions_dataset, rebuild_extensions_dataset, refresh_dataset
from .remote import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    EQEMU_REMOTE_MAP_SCOPES,
    WRITE_HISTORY_LIMIT,
    WRITE_HISTORY_MAX_BYTES,
    delete_remote_file,
    garbage_collect_write_history,
    map_remote_eqemu_server,
    list_profiles as list_remote_profiles,
    list_remote_files,
    list_write_history,
    onboarding_payload as remote_onboarding_payload,
    preview_write_undo,
    set_profile_read_only_mode,
    stage_remote_file,
    test_connection as test_remote_connection,
    trust_ftps_certificate,
    undo_write_operation,
    upload_staged_file,
)
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
        digest = extension_inputs_digest(EXTENSIONS_ROOT, LOCAL_EXTENSIONS_ROOT)
        manifest = self.store.manifest() if hasattr(self.store, "manifest") else {}
        manifest_inputs = manifest.get("extension_inputs", {}) if isinstance(manifest, dict) else {}
        active_manifest_digest = manifest_inputs.get("digest") if isinstance(manifest_inputs, dict) else None
        has_active_local_extensions = any(load_domain_extensions(LOCAL_EXTENSIONS_ROOT, domain) for domain in DOMAIN_CHOICES)
        using_overlay = "overlay" in str(self.store.data_root)
        should_rebuild_overlay = (
            (active_manifest_digest is not None and active_manifest_digest != digest)
            or (active_manifest_digest is None and using_overlay)
            or (has_active_local_extensions and not using_overlay)
        )
        if should_rebuild_overlay:
            rebuild_extensions_dataset(scope="all", mode="overlay")
            self.store = DataStore()
        self._extension_validation_fingerprint = fingerprint
        self._extension_validation_error = None
        self._schema_extension_health_fingerprint = None
        self._schema_extension_health = None

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
                "description": "Search staged EQEmu quest API, schema, docs, quest example sources, and Perl plugin example sources.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "domains": {"type": "array", "items": {"type": "string", "enum": list(DOMAIN_CHOICES)}},
                        "language": {"type": "string", "enum": list(QUEST_LANGUAGE_CHOICES)},
                        "limit": {"type": "integer", "minimum": 1},
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
                        "language": {"type": "string", "enum": list(QUEST_LANGUAGE_CHOICES)},
                        "kind": {"type": "string", "enum": list(QUEST_KIND_CHOICES)},
                        "name": {"type": "string"},
                        "group_or_type": {"type": "string"},
                        "signature": {"type": "string"},
                        "params": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["language", "kind", "name"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "get_quest_api_overloads",
                "description": "Get all overloads for a quest API method, event, or constant, optionally filtered by signature or params.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string", "enum": list(QUEST_LANGUAGE_CHOICES)},
                        "kind": {"type": "string", "enum": list(QUEST_KIND_CHOICES)},
                        "name": {"type": "string"},
                        "group_or_type": {"type": "string"},
                        "signature": {"type": "string"},
                        "params": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["language", "kind", "name"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "summarize_quest_api_topic",
                "description": "Summarize a broad quest scripting topic with scripting-oriented examples and grouped APIs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "language": {"type": "string", "enum": list(QUEST_LANGUAGE_CHOICES)},
                        "limit": {"type": "integer", "minimum": 1}
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
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "explain_db_relationships",
                "description": "Explain outbound and inbound EQEmu schema relationships for a table as a small graph.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string"},
                        "depth": {"type": "integer", "minimum": 1}
                    },
                    "required": ["table_name"]
                },
                "annotations": {"readOnlyHint": True}
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
                        "domain": {"type": "string", "enum": list(DOMAIN_CHOICES)},
                        "id": {"type": "string"}
                    },
                    "required": ["domain", "id"]
                }
            },
            {
                "name": "refresh_eqemu_oracle",
                "description": "Refresh upstream data into an overlay workspace and rebuild merged data. Requires confirm_write=true.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": list(SCOPE_CHOICES)},
                        "mode": {"type": "string", "enum": list(MODE_CHOICES)},
                        "confirm_write": {"type": "boolean"}
                    }
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "rebuild_eqemu_extensions",
                "description": "Rebuild merged data and the search cache from existing base data plus extensions. Requires confirm_write=true.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"scope": {"type": "string", "enum": list(SCOPE_CHOICES)}, "mode": {"type": "string", "enum": list(MODE_CHOICES)}, "confirm_write": {"type": "boolean"}}
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "prune_stale_schema_extensions",
                "description": "Preview or remove schema extension entries that appear stale because upstream schema already covers them.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "apply": {"type": "boolean"},
                        "confirm_write": {"type": "boolean"}
                    }
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "update_eqemu_oracle_plugin",
                "description": "Pull the EQEmu Oracle plugin repo from Git and rebuild committed merged data. Requires confirm_write=true.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "remote": {"type": "string"},
                        "branch": {"type": "string"},
                        "allow_dirty": {"type": "boolean"},
                        "skip_rebuild": {"type": "boolean"},
                        "restore_branch": {"type": "boolean"},
                        "confirm_write": {"type": "boolean"}
                    }
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "get_eqemu_example_file",
                "description": "Get a cached quest or Perl plugin example file previously indexed from configured example sources.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "enum": ["quests", "plugins"]},
                        "id": {"type": "string"}
                    },
                    "required": ["domain", "id"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "get_eqemu_server_ftp_onboarding",
                "description": "Show the safe local onboarding flow for creating a stored EQEmu server FTP/FTPS profile without pasting passwords into chat.",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "list_eqemu_server_ftp_profiles",
                "description": "List locally configured EQEmu server FTP/FTPS profiles. Credentials are never returned.",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "test_eqemu_server_ftp_connection",
                "description": "Test a locally configured EQEmu server FTP/FTPS profile by opening a connection and reading a small root listing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"profile": {"type": "string"}},
                    "required": ["profile"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "trust_eqemu_server_ftp_certificate",
                "description": "Preview or pin the currently presented FTPS certificate SHA-256 fingerprint for a profile. Confirmed pinning changes local trust metadata and must only be run after explicit user instruction and exact fingerprint confirmation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "confirm_trust": {"type": "boolean"},
                        "confirm_sha256": {"type": "string"}
                    },
                    "required": ["profile"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "list_eqemu_server_ftp_files",
                "description": "List files on a configured remote EQEmu server FTP/FTPS profile. The path is constrained to the profile root.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "remote_path": {"type": "string"},
                        "recursive": {"type": "boolean"},
                        "limit": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "map_eqemu_server_ftp_layout",
                "description": "Read-only map of a configured remote EQEmu server FTP/FTPS layout. Classifies known EQEmu folders, quest/plugin/log paths, zone folders, global item/spell scripts, NPC name/id scripts, and Lua-over-Perl priority conflicts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "remote_path": {"type": "string"},
                        "scope": {"type": "string", "enum": list(EQEMU_REMOTE_MAP_SCOPES)},
                        "zone": {"type": "string"},
                        "max_depth": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "stage_eqemu_server_ftp_file",
                "description": "Download one remote EQEmu server file into the local staging area for review or edit. Existing staged edits are preserved by versioning unless a different overwrite_policy is supplied.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "remote_path": {"type": "string"},
                        "overwrite_policy": {"type": "string", "enum": ["versioned", "overwrite", "fail"]},
                        "max_bytes": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile", "remote_path"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": False}
            },
            {
                "name": "upload_eqemu_server_ftp_file",
                "description": "Upload a staged local file back to the configured remote EQEmu server. Requires confirm_write=true and confirm_remote_path matching the resolved target path; creates a local restore point first, verifies the remote file after upload, and returns a write-history operation id for undo. Creating a new remote file additionally requires allow_create=true.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "local_path": {"type": "string"},
                        "remote_path": {"type": "string"},
                        "confirm_write": {"type": "boolean"},
                        "confirm_remote_path": {"type": "string"},
                        "allow_create": {"type": "boolean"},
                        "allow_remote_changed": {"type": "boolean"},
                        "max_backup_bytes": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile", "local_path"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "delete_eqemu_server_ftp_file",
                "description": "Delete one remote FTP/FTPS file after exact confirmation. Preview downloads the target for hash review; confirmed delete requires confirm_delete=true, exact confirm_remote_path, exact confirm_remote_sha256, saves a local restore point first, verifies the file is gone, and records a write-history operation id for undo.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "remote_path": {"type": "string"},
                        "confirm_delete": {"type": "boolean"},
                        "confirm_remote_path": {"type": "string"},
                        "confirm_remote_sha256": {"type": "string"},
                        "max_backup_bytes": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile", "remote_path"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "list_eqemu_server_ftp_write_history",
                "description": "List recent guarded remote FTP/FTPS writes and undo restore points for a configured EQEmu server profile.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "set_eqemu_server_ftp_read_only_mode",
                "description": "Preview or set a configured FTP/FTPS profile's persisted read-only mode. Confirmed mode changes must only be run after explicit user instruction and exact confirmation fields; read-only mode blocks upload, undo, and undo garbage-collection apply.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "read_only": {"type": "boolean"},
                        "confirm_mode_change": {"type": "boolean"},
                        "confirm_profile": {"type": "string"},
                        "confirm_read_only_mode": {"type": "string", "enum": ["read-only", "read-write"]}
                    },
                    "required": ["profile", "read_only"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "garbage_collect_eqemu_server_ftp_write_history",
                "description": "Preview or apply local garbage collection for FTP/FTPS undo restore points beyond the retention policy. This never touches the remote server; apply mode deletes local restore-point files and requires confirm_write=true.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "apply": {"type": "boolean"},
                        "confirm_write": {"type": "boolean"},
                        "prune_orphans": {"type": "boolean"},
                        "max_operations": {"type": "integer", "minimum": 1},
                        "max_bytes": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            },
            {
                "name": "preview_eqemu_server_ftp_undo",
                "description": "Preview restoring a previous remote FTP/FTPS write from its local restore-point backup. This is read-only and returns the exact confirmation arguments for undo.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "operation_id": {"type": "string"},
                        "check_remote": {"type": "boolean"},
                        "max_bytes": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile", "operation_id"]
                },
                "annotations": {"readOnlyHint": True}
            },
            {
                "name": "undo_eqemu_server_ftp_write",
                "description": "Restore a previous remote FTP/FTPS write from its local restore-point backup. Requires confirm_write=true and confirm_operation_id matching the operation id; creates a new restore point first.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "operation_id": {"type": "string"},
                        "confirm_write": {"type": "boolean"},
                        "confirm_operation_id": {"type": "string"},
                        "allow_remote_changed": {"type": "boolean"},
                        "max_bytes": {"type": "integer", "minimum": 1}
                    },
                    "required": ["profile", "operation_id"]
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True}
            }
        ]

    def _resources(self) -> list[dict[str, Any]]:
        return [
            {"uri": "eqemu://manifest", "name": "Manifest", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/quest-api", "name": "Quest API Index", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/schema", "name": "Schema Index", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/docs", "name": "Docs Index", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/docs-sections", "name": "Docs Sections Index", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/quests", "name": "Quest Example Sources", "mimeType": "application/json"},
            {"uri": "eqemu://indexes/plugins", "name": "Perl Plugin Example Sources", "mimeType": "application/json"},
        ]

    def _resource_templates(self) -> list[dict[str, Any]]:
        return [
            {"uriTemplate": "eqemu://quest-api/{id}", "name": "Quest API Entry", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://schema/table/{table_name}", "name": "Schema Table", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://docs/page/{path}", "name": "Docs Page", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://quests/source/{id}", "name": "Quest Example Source", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://plugins/source/{id}", "name": "Perl Plugin Example Source", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://quests/example/{id}", "name": "Quest Example File", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://plugins/example/{id}", "name": "Perl Plugin Example File", "mimeType": "application/json"},
            {"uriTemplate": "eqemu://provenance/{domain}/{id}", "name": "Record Provenance", "mimeType": "application/json"},
        ]

    def _confirmation_required(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "requires_confirmation": True,
            "tool": tool_name,
            "message": "This EQEmu Oracle maintenance tool can modify local plugin data or Git state. Re-run with confirm_write=true to proceed.",
            "requested_arguments": {key: value for key, value in arguments.items() if key != "confirm_write"},
            "confirmation_argument": {"confirm_write": True},
        }

    def _handle_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name in {
            "search_eqemu_context",
            "get_quest_api_entry",
            "get_quest_api_overloads",
            "summarize_quest_api_topic",
            "get_db_table",
            "explain_db_relationships",
            "get_doc_page",
            "explain_eqemu_provenance",
            "get_eqemu_example_file",
        }:
            self._preflight_extensions()
        if name == "search_eqemu_context":
            domains = self._enum_list(arguments, "domains", DOMAIN_CHOICES)
            result = self.store.search(
                arguments["query"],
                domains,
                self._int_arg(arguments, "limit", 10, minimum=1),
                self._bool_arg(arguments, "include_extensions", True),
                self._enum_arg(arguments, "language", QUEST_LANGUAGE_CHOICES, default=None, allow_none=True),
                self._bool_arg(arguments, "prefer_fresh", False),
            )
        elif name == "get_quest_api_entry":
            result = self.store.get_quest_entry(
                self._enum_arg(arguments, "language", QUEST_LANGUAGE_CHOICES),
                self._enum_arg(arguments, "kind", QUEST_KIND_CHOICES),
                arguments["name"],
                arguments.get("group_or_type"),
                arguments.get("signature"),
                self._string_list_arg(arguments, "params", default=None),
            )
        elif name == "get_quest_api_overloads":
            result = self.store.get_quest_overloads(
                self._enum_arg(arguments, "language", QUEST_LANGUAGE_CHOICES),
                self._enum_arg(arguments, "kind", QUEST_KIND_CHOICES),
                arguments["name"],
                arguments.get("group_or_type"),
                arguments.get("signature"),
                self._string_list_arg(arguments, "params", default=None),
            )
        elif name == "summarize_quest_api_topic":
            result = self.store.summarize_quest_topic(
                arguments["query"],
                self._enum_arg(arguments, "language", QUEST_LANGUAGE_CHOICES, default="perl"),
                self._int_arg(arguments, "limit", 16, minimum=1),
            )
        elif name == "get_db_table":
            result = self.store.get_table(arguments["table_name"])
        elif name == "explain_db_relationships":
            result = self.store.explain_table_relationships(
                arguments["table_name"],
                self._int_arg(arguments, "depth", 1, minimum=1),
            )
        elif name == "get_doc_page":
            result = self.store.get_doc_page(arguments["path_or_slug"])
        elif name == "explain_eqemu_provenance":
            result = self.store.explain_provenance(self._enum_arg(arguments, "domain", DOMAIN_CHOICES), arguments["id"])
        elif name == "refresh_eqemu_oracle":
            if not self._bool_arg(arguments, "confirm_write", False):
                result = self._confirmation_required(name, arguments)
            else:
                result = refresh_dataset(
                    scope=self._enum_arg(arguments, "scope", SCOPE_CHOICES, default="all"),
                    mode=self._enum_arg(arguments, "mode", MODE_CHOICES, default="overlay"),
                )
                self.store = DataStore()
                self._reset_extension_validation()
        elif name == "rebuild_eqemu_extensions":
            if not self._bool_arg(arguments, "confirm_write", False):
                result = self._confirmation_required(name, arguments)
            else:
                result = rebuild_extensions_dataset(
                    scope=self._enum_arg(arguments, "scope", SCOPE_CHOICES, default="all"),
                    mode=self._enum_arg(arguments, "mode", MODE_CHOICES, default="overlay"),
                )
                self.store = DataStore()
                self._reset_extension_validation()
        elif name == "prune_stale_schema_extensions":
            apply = self._bool_arg(arguments, "apply", False)
            if apply and not self._bool_arg(arguments, "confirm_write", False):
                result = self._confirmation_required(name, arguments)
            else:
                result, _manifest = prune_schema_extensions_dataset(
                    apply=apply,
                    mode="overlay" if "overlay" in str(self.store.data_root) else "committed",
                )
                if apply and result.get("removed_count"):
                    self.store = DataStore()
                self._reset_extension_validation()
        elif name == "update_eqemu_oracle_plugin":
            if not self._bool_arg(arguments, "confirm_write", False):
                result = self._confirmation_required(name, arguments)
            else:
                result = update_plugin_repo(
                    remote=str(arguments.get("remote", "origin")),
                    branch=arguments.get("branch"),
                    allow_dirty=self._bool_arg(arguments, "allow_dirty", False),
                    skip_rebuild=self._bool_arg(arguments, "skip_rebuild", False),
                    restore_branch=self._bool_arg(arguments, "restore_branch", False),
                )
                self.store = DataStore()
                self._reset_extension_validation()
        elif name == "get_eqemu_example_file":
            result = self.store.get_example_file(arguments["domain"], arguments["id"])
        elif name == "get_eqemu_server_ftp_onboarding":
            result = remote_onboarding_payload()
        elif name == "list_eqemu_server_ftp_profiles":
            result = list_remote_profiles()
        elif name == "test_eqemu_server_ftp_connection":
            result = test_remote_connection(arguments["profile"])
        elif name == "trust_eqemu_server_ftp_certificate":
            result = trust_ftps_certificate(
                arguments["profile"],
                confirm_trust=self._bool_arg(arguments, "confirm_trust", False),
                confirm_sha256=arguments.get("confirm_sha256"),
            )
        elif name == "list_eqemu_server_ftp_files":
            result = list_remote_files(
                arguments["profile"],
                remote_path=str(arguments.get("remote_path", ".")),
                recursive=self._bool_arg(arguments, "recursive", False),
                limit=self._int_arg(arguments, "limit", 100, minimum=1),
            )
        elif name == "map_eqemu_server_ftp_layout":
            result = map_remote_eqemu_server(
                arguments["profile"],
                remote_path=str(arguments["remote_path"]) if "remote_path" in arguments else None,
                scope=str(arguments.get("scope", "auto")),
                zone=str(arguments["zone"]) if "zone" in arguments else None,
                max_depth=self._int_arg(arguments, "max_depth", 0, minimum=0) if "max_depth" in arguments else None,
                limit=self._int_arg(arguments, "limit", 1000, minimum=1),
            )
        elif name == "stage_eqemu_server_ftp_file":
            result = stage_remote_file(
                arguments["profile"],
                remote_path=arguments["remote_path"],
                overwrite_policy=str(arguments.get("overwrite_policy", "versioned")),
                max_bytes=self._int_arg(arguments, "max_bytes", DEFAULT_MAX_DOWNLOAD_BYTES, minimum=1),
            )
        elif name == "upload_eqemu_server_ftp_file":
            result = upload_staged_file(
                arguments["profile"],
                local_path=arguments["local_path"],
                remote_path=arguments.get("remote_path"),
                confirm_write=self._bool_arg(arguments, "confirm_write", False),
                confirm_remote_path=arguments.get("confirm_remote_path"),
                create_backup=True,
                allow_create=self._bool_arg(arguments, "allow_create", False),
                allow_remote_changed=self._bool_arg(arguments, "allow_remote_changed", False),
                max_backup_bytes=self._int_arg(arguments, "max_backup_bytes", DEFAULT_MAX_DOWNLOAD_BYTES, minimum=1),
            )
        elif name == "delete_eqemu_server_ftp_file":
            result = delete_remote_file(
                arguments["profile"],
                remote_path=arguments["remote_path"],
                confirm_delete=self._bool_arg(arguments, "confirm_delete", False),
                confirm_remote_path=arguments.get("confirm_remote_path"),
                confirm_remote_sha256=arguments.get("confirm_remote_sha256"),
                max_backup_bytes=self._int_arg(arguments, "max_backup_bytes", DEFAULT_MAX_DOWNLOAD_BYTES, minimum=1),
            )
        elif name == "list_eqemu_server_ftp_write_history":
            result = list_write_history(
                arguments["profile"],
                limit=self._int_arg(arguments, "limit", 25, minimum=1),
            )
        elif name == "set_eqemu_server_ftp_read_only_mode":
            result = set_profile_read_only_mode(
                arguments["profile"],
                read_only=self._bool_arg(arguments, "read_only", False),
                confirm_mode_change=self._bool_arg(arguments, "confirm_mode_change", False),
                confirm_profile=arguments.get("confirm_profile"),
                confirm_read_only_mode=arguments.get("confirm_read_only_mode"),
            )
        elif name == "garbage_collect_eqemu_server_ftp_write_history":
            result = garbage_collect_write_history(
                arguments["profile"],
                apply=self._bool_arg(arguments, "apply", False),
                confirm_write=self._bool_arg(arguments, "confirm_write", False),
                prune_orphans=self._bool_arg(arguments, "prune_orphans", True),
                max_operations=self._int_arg(arguments, "max_operations", WRITE_HISTORY_LIMIT, minimum=1),
                max_bytes=self._int_arg(arguments, "max_bytes", WRITE_HISTORY_MAX_BYTES, minimum=1),
            )
        elif name == "preview_eqemu_server_ftp_undo":
            result = preview_write_undo(
                arguments["profile"],
                operation_id=arguments["operation_id"],
                check_remote=self._bool_arg(arguments, "check_remote", True),
                max_bytes=self._int_arg(arguments, "max_bytes", DEFAULT_MAX_DOWNLOAD_BYTES, minimum=1),
            )
        elif name == "undo_eqemu_server_ftp_write":
            result = undo_write_operation(
                arguments["profile"],
                operation_id=arguments["operation_id"],
                confirm_write=self._bool_arg(arguments, "confirm_write", False),
                confirm_operation_id=arguments.get("confirm_operation_id"),
                allow_remote_changed=self._bool_arg(arguments, "allow_remote_changed", False),
                max_bytes=self._int_arg(arguments, "max_bytes", DEFAULT_MAX_DOWNLOAD_BYTES, minimum=1),
            )
        else:
            raise ValueError(f"Unknown tool '{name}'")
        if name in {
            "search_eqemu_context",
            "get_quest_api_entry",
            "get_quest_api_overloads",
            "summarize_quest_api_topic",
            "get_db_table",
            "explain_db_relationships",
            "get_doc_page",
            "explain_eqemu_provenance",
            "get_eqemu_example_file",
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

    def _enum_arg(
        self,
        arguments: dict[str, Any],
        name: str,
        allowed: tuple[str, ...],
        *,
        default: str | None = None,
        allow_none: bool = False,
    ) -> str | None:
        value = arguments.get(name, default)
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"Invalid `{name}` value '{value}'. Expected one of: {', '.join(allowed)}")
        return value

    def _enum_list(self, arguments: dict[str, Any], name: str, allowed: tuple[str, ...]) -> list[str] | None:
        value = arguments.get(name)
        if value is None:
            return None
        if not isinstance(value, list) or any(not isinstance(item, str) or item not in allowed for item in value):
            raise ValueError(f"Invalid `{name}` values. Expected a list drawn from: {', '.join(allowed)}")
        return value

    def _string_list_arg(self, arguments: dict[str, Any], name: str, default: list[str] | None = None) -> list[str] | None:
        value = arguments.get(name, default)
        if value is None:
            return None
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Invalid `{name}` value. Expected a list of strings.")
        return value

    def _bool_arg(self, arguments: dict[str, Any], name: str, default: bool) -> bool:
        value = arguments.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"Invalid `{name}` value '{value}'. Expected a boolean.")
        return value

    def _int_arg(self, arguments: dict[str, Any], name: str, default: int, *, minimum: int) -> int:
        value = arguments.get(name, default)
        if not isinstance(value, int) or value < minimum:
            raise ValueError(f"Invalid `{name}` value '{value}'. Expected an integer >= {minimum}.")
        return value

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
        elif uri == "eqemu://indexes/docs-sections":
            payload = self.store.docs_sections
        elif uri == "eqemu://indexes/quests":
            payload = self.store.source_index("quests")
        elif uri == "eqemu://indexes/plugins":
            payload = self.store.source_index("plugins")
        elif uri.startswith("eqemu://quest-api/"):
            payload = self.store.get_quest_entry_by_id(uri.removeprefix("eqemu://quest-api/"))
        elif uri.startswith("eqemu://schema/table/"):
            payload = self.store.get_table(uri.removeprefix("eqemu://schema/table/"))
        elif uri.startswith("eqemu://docs/page/"):
            payload = self.store.get_doc_page(uri.removeprefix("eqemu://docs/page/"))
        elif uri.startswith("eqemu://quests/source/"):
            source_id = uri.removeprefix("eqemu://quests/source/")
            payload = next((item for item in self.store.source_index("quests") if item.get("id") == source_id), None)
        elif uri.startswith("eqemu://plugins/source/"):
            source_id = uri.removeprefix("eqemu://plugins/source/")
            payload = next((item for item in self.store.source_index("plugins") if item.get("id") == source_id), None)
        elif uri.startswith("eqemu://quests/example/"):
            example_id = uri.removeprefix("eqemu://quests/example/")
            payload = self.store.get_example_file("quests", example_id)
        elif uri.startswith("eqemu://plugins/example/"):
            example_id = uri.removeprefix("eqemu://plugins/example/")
            payload = self.store.get_example_file("plugins", example_id)
        elif uri.startswith("eqemu://provenance/"):
            _, _, remainder = uri.partition("eqemu://provenance/")
            domain, separator, record_id = remainder.partition("/")
            if not separator or not record_id:
                raise ValueError(f"Unknown resource '{uri}'")
            payload = self.store.explain_provenance(domain, record_id)
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
