#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ORACLE_TOOL_NAMES = (
    "search_eqemu_context",
    "get_quest_api_entry",
    "get_quest_api_overloads",
    "summarize_quest_api_topic",
    "get_db_table",
    "explain_db_relationships",
    "get_doc_page",
    "get_eqemu_example_file",
    "explain_eqemu_provenance",
    "refresh_eqemu_oracle",
    "rebuild_eqemu_extensions",
    "prune_stale_schema_extensions",
    "update_eqemu_oracle_plugin",
)

MAINTENANCE_COMMANDS = (
    "refresh",
    "rebuild-extensions",
    "prune-stale-schema-extensions",
    "update-plugin",
    "build-release-bundle",
)

CLI_TOOL_MARKERS = (
    "eqemu_oracle.py tool ",
    "eqemu_oracle_launcher.cmd tool ",
)

VALIDATION_MARKERS = (
    "rebuild-extensions",
    "rebuild_eqemu_extensions",
    "refresh_eqemu_oracle",
    "refresh --scope",
    "prune-stale-schema-extensions",
    "test_extensions.py",
    "test_runtime_smoke.py",
    "test_mcp.py",
    "pytest",
    "unittest",
)

PROVENANCE_MARKERS = (
    "eqemu oracle",
    "quest api",
    "quest-api",
    "schema",
    "table",
    "docs",
    "documentation",
    "page",
    "source",
    "provenance",
    "record",
    "entry",
    "method",
    "event",
    "constant",
)

EXPLICIT_INVOCATION_PATTERNS = (
    re.compile(r"@eqemu[\s_-]*oracle\b", re.IGNORECASE),
    re.compile(r"\b(use|using|invoke|invoking|ask|query|check|consult)\s+eqemu[\s_-]*oracle\b", re.IGNORECASE),
    re.compile(r"\beqemu[\s_-]*oracle\s*:\s*", re.IGNORECASE),
)

EXTENSION_PATH_RE = re.compile(r"plugins/eqemu-oracle/(?:local-)?extensions/|(?:^|\s)(?:local-)?extensions/", re.IGNORECASE)
EDIT_MARKER_RE = re.compile(r"apply_patch|Update File:|Add File:|Delete File:|write_text|tool_use", re.IGNORECASE)
PLUGIN_MECHANICS_RE = re.compile(r"\b(hook|hooks|manifest|install|installer|release|repo|repository|codex plugin|plugin\.json|hooks\.json)\b", re.IGNORECASE)
EQEMU_DOMAIN_RE = re.compile(
    r"\b(quest|schema|table|npc|spawn|perl|lua|event|method|docs page|rule|item|spell|zone|plugin::)\b",
    re.IGNORECASE,
)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = _read_payload()
    if mode == "stop":
        return _handle_stop(payload)
    if mode == "post-tool-use":
        return _handle_post_tool_use(payload)
    return _emit_json(
        {
            "continue": False,
            "stopReason": "Unknown EQEmu Oracle hook mode.",
            "systemMessage": f"Unknown EQEmu Oracle hook mode: {mode or '<missing>'}",
        }
    )


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _handle_stop(payload: dict[str, Any]) -> int:
    if payload.get("stop_hook_active"):
        return 0

    transcript = _read_transcript(payload.get("transcript_path"))
    last_message = str(payload.get("last_assistant_message") or "")

    if _extension_overlay_touched(transcript) and not _has_validation_evidence(transcript):
        return _continue_turn(
            "EQEmu Oracle extension overlay files appear to have changed, but this turn does not show a rebuild or validation run. Run the appropriate rebuild or tests, or explicitly explain why validation is unnecessary."
        )

    if not _explicitly_invoked(transcript):
        return 0

    if _plugin_mechanics_request(transcript):
        return 0

    if not _oracle_tool_used(transcript):
        return _continue_turn(
            "The user explicitly invoked EQEmu Oracle, but this turn does not show EQEmu Oracle tool usage. Call the relevant EQEmu Oracle MCP tool before answering, unless the response is only about plugin mechanics."
        )

    if _substantive_answer(last_message) and not _has_provenance_marker(last_message):
        return _continue_turn(
            "The EQEmu Oracle answer should mention the source type or record used, such as quest API entry, schema table, docs page, or provenance. Revise the answer with concise source context."
        )

    return 0


def _handle_post_tool_use(payload: dict[str, Any]) -> int:
    command = _nested_get(payload, ("tool_input", "command"))
    command_text = str(command or "")
    if not _is_eqemu_maintenance_command(command_text):
        return 0

    response_text = _stringify(payload.get("tool_response"))
    lower_response = response_text.lower()
    if _looks_like_command_failure(command_text, response_text):
        return _post_tool_feedback(
            "EQEmu Oracle maintenance command output looks unsuccessful. Inspect the error, fix the issue, and rerun the command before relying on generated plugin data."
        )

    if "build-release-bundle" in command_text and "archive_path" not in lower_response:
        return _post_tool_feedback(
            "The release bundle command ran, but the output did not include the expected archive_path JSON. Verify the bundle was created before continuing."
        )

    return 0


def _read_transcript(path_value: Any) -> str:
    if not path_value:
        return ""
    try:
        path = Path(str(path_value)).expanduser()
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _explicitly_invoked(transcript: str) -> bool:
    for message in _recent_user_messages(transcript):
        if any(pattern.search(message) for pattern in EXPLICIT_INVOCATION_PATTERNS):
            return True
    tail = transcript[-12000:]
    return any(pattern.search(tail) for pattern in EXPLICIT_INVOCATION_PATTERNS)


def _recent_user_messages(transcript: str) -> list[str]:
    messages: list[str] = []
    for obj in _iter_json_objects(transcript):
        role = _find_role(obj)
        if role == "user":
            text = _extract_text(obj)
            if text:
                messages.append(text)
    return messages[-6:]


def _plugin_mechanics_request(transcript: str) -> bool:
    for message in _recent_user_messages(transcript):
        if any(pattern.search(message) for pattern in EXPLICIT_INVOCATION_PATTERNS):
            return bool(PLUGIN_MECHANICS_RE.search(message) and not EQEMU_DOMAIN_RE.search(message))
    return False


def _iter_json_objects(text: str) -> list[Any]:
    objects: list[Any] = []
    stripped = text.strip()
    if not stripped:
        return objects
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    for line in stripped.splitlines():
        try:
            objects.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return objects


def _find_role(value: Any) -> str | None:
    if isinstance(value, dict):
        role = value.get("role")
        if isinstance(role, str):
            return role.lower()
        for nested in value.values():
            found = _find_role(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_role(nested)
            if found:
                return found
    return None


def _extract_text(value: Any) -> str:
    chunks: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict):
            for key, nested in item.items():
                if key in {"content", "text", "message", "prompt"}:
                    walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
    return "\n".join(chunks)


def _oracle_tool_used(transcript: str) -> bool:
    lower_transcript = transcript.lower()
    return any(tool.lower() in lower_transcript for tool in ORACLE_TOOL_NAMES) or any(
        marker in lower_transcript for marker in CLI_TOOL_MARKERS
    )


def _substantive_answer(message: str) -> bool:
    words = re.findall(r"\w+", message)
    return len(words) >= 24


def _has_provenance_marker(message: str) -> bool:
    lower_message = message.lower()
    return any(marker in lower_message for marker in PROVENANCE_MARKERS)


def _extension_overlay_touched(transcript: str) -> bool:
    tail = transcript[-20000:]
    return bool(EXTENSION_PATH_RE.search(tail) and EDIT_MARKER_RE.search(tail))


def _has_validation_evidence(transcript: str) -> bool:
    lower_tail = transcript[-30000:].lower()
    return any(marker.lower() in lower_tail for marker in VALIDATION_MARKERS)


def _is_eqemu_maintenance_command(command: str) -> bool:
    lower_command = command.lower()
    if "eqemu_oracle.py" not in lower_command and "eqemu_oracle_launcher.cmd" not in lower_command:
        return False
    return any(command_name in lower_command for command_name in MAINTENANCE_COMMANDS)


def _looks_like_command_failure(command: str, response: str) -> bool:
    lower_response = response.lower()
    if any(marker in lower_response for marker in ("traceback", "extensionvalidationerror", "runtimeerror", "error:", "failed")):
        return True
    if re.search(r"\b(exit|return)\s*code\s*[:=]?\s*[1-9]\d*\b", lower_response):
        return True
    if "process exited with code 0" in lower_response:
        return False
    if "build-release-bundle" in command and "archive_path" in lower_response:
        return False
    return False


def _nested_get(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def _continue_turn(reason: str) -> int:
    return _emit_json({"decision": "block", "reason": reason})


def _post_tool_feedback(reason: str) -> int:
    return _emit_json(
        {
            "continue": False,
            "stopReason": reason,
            "systemMessage": reason,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": reason,
            },
        }
    )


def _emit_json(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
