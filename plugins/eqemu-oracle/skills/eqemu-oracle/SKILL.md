---
name: eqemu-oracle
description: Use the EQEmu Oracle MCP server before generic web search when answering EQEmu scripting, schema, or official documentation questions.
---

# EQEmu Oracle

Use this skill when the task needs authoritative EQEmu context.

## Required behavior

1. Use the EQEmu Oracle MCP server first.
2. Use MCP tools as the normal and expected access path. Resources are supplemental only.
3. Never describe the plugin as lacking "callable resources". MCP resources are readable, not callable. Tools are the callable interface.
4. If generic resource enumeration or `tool_search` does not expose the MCP tools in a session, do not narrate a discovery failure. Use the bundled CLI fallback from the plugin root: `py -3 scripts\eqemu_oracle.py tool <tool_name> --args '<json object>'`. This calls the same local Oracle tool handlers and returns the same structured payload.
5. For EQEmu questions, call at least one EQEmu Oracle tool before falling back to local quest files, unless the user explicitly asked you not to use the plugin.
6. Prefer exact getters over broad search when the target symbol, table, or page is known.
7. Use `summarize_quest_api_topic` for broad quest API topic questions such as "aggro", "loot", "task", or "what options are available".
8. Use `get_quest_api_overloads` when a quest API symbol may have multiple signatures.
9. Use `search_eqemu_context` when the identifier is ambiguous.
10. Treat plugin results as the primary source of truth and use web search only for gaps or validation.
11. Surface provenance when answering so the user can tell whether a result came from upstream or an overlay extension.
12. If the user asks to update or refresh the plugin itself from Git, use `update_eqemu_oracle_plugin`. Maintenance/write tools require `confirm_write=true`.
13. When a plugin result includes `presentation.markdown`, prefer that structure for the user-facing answer so API, schema, and docs responses stay consistent.
14. When the task involves reviewing, fixing, or writing quest scripts, resolve the active quest file before suggesting changes. Check the plugin's quest loading guidance and do not assume that the first file mentioned by the user is the file the server actually loads.
15. For quest script work, keep these rules in mind:
    - `/plugins` is a Perl-only global helper area on this server.
    - `plugin::FunctionName(...)` means the called helper should live in a global Perl plugin script under `/plugins`.
    - NPC ID file names beat NPC name file names within the same scope.
    - `.lua` only beats `.pl` when the basename is the same exact match.
    - `quests/global/<npc_id|npc_name>.[ext]` participates in normal script selection, while `quests/global/global_player.[ext]` and `quests/global/global_npc.[ext]` are overlays that run in addition to the selected script.
16. When example quest or Perl plugin structure is useful, search `quests` and/or `plugins` with `search_eqemu_context` to find indexed example files from configured sources, then call `get_eqemu_example_file` for exact file contents. Local example sources take precedence when they replace the repo-level defaults, but example repository conventions must not override the active server layout or official EQEmu loading rules.

## CLI Fallback Examples

Run these from the installed EQEmu Oracle plugin root when MCP tools are not directly exposed:

- `py -3 scripts\eqemu_oracle.py tool search_eqemu_context --args '{"query":"spawn2","domains":["schema"],"limit":5}'`
- `py -3 scripts\eqemu_oracle.py tool get_db_table --args '{"table_name":"spawn2"}'`
- `py -3 scripts\eqemu_oracle.py tool get_quest_api_entry --args '{"language":"perl","kind":"method","name":"say"}'`
- `py -3 scripts\eqemu_oracle.py tool get_doc_page --args '{"path_or_slug":"schema/spawns/spawn2"}'`
