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
4. If generic resource enumeration is missing in a session, continue using the plugin tools and do not say the plugin is unavailable.
5. For EQEmu questions, call at least one EQEmu Oracle tool before falling back to local quest files, unless the user explicitly asked you not to use the plugin.
6. Prefer exact getters over broad search when the target symbol, table, or page is known.
7. Use `summarize_quest_api_topic` for broad quest API topic questions such as "aggro", "loot", "task", or "what options are available".
8. Use `search_eqemu_context` when the identifier is ambiguous.
9. Treat plugin results as the primary source of truth and use web search only for gaps or validation.
10. Surface provenance when answering so the user can tell whether a result came from upstream or an overlay extension.
11. If the user asks to update or refresh the plugin itself from Git, use `update_eqemu_oracle_plugin`.
12. When a plugin result includes `presentation.markdown`, prefer that structure for the user-facing answer so API, schema, and docs responses stay consistent.
13. When the task involves reviewing, fixing, or writing quest scripts, resolve the active quest file before suggesting changes. Check the plugin's quest loading guidance and do not assume that the first file mentioned by the user is the file the server actually loads.
14. For quest script work, keep these rules in mind:
    - `/plugins` is a Perl-only global helper area on this server.
    - `plugin::FunctionName(...)` means the called helper should live in a global Perl plugin script under `/plugins`.
    - NPC ID file names beat NPC name file names within the same scope.
    - `.lua` only beats `.pl` when the basename is the same exact match.
    - `quests/global/<npc_id|npc_name>.[ext]` participates in normal script selection, while `quests/global/global_player.[ext]` and `quests/global/global_npc.[ext]` are overlays that run in addition to the selected script.
15. When example quest or Perl plugin structure is useful, search `quests` and/or `plugins` with `search_eqemu_context` to find the configured example sources. Local example sources take precedence when they replace the repo-level defaults, but example repository conventions must not override the active server layout or official EQEmu loading rules.
