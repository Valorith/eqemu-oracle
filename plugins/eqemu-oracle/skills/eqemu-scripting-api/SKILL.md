---
name: eqemu-scripting-api
description: Look up EQEmu Perl and Lua quest API methods, events, and constants through EQEmu Oracle.
---

# EQEmu Scripting API

1. If the language, kind, and symbol are known, call `get_quest_api_entry`.
2. If a method has multiple signatures or the user asks which overloads exist, call `get_quest_api_overloads`.
3. If the user asks for a broad topic, family, or "what options are available", call `summarize_quest_api_topic`.
4. Otherwise call `search_eqemu_context` with `domains=["quest-api"]`.
4. Treat the plugin tools as the normal access path.
5. Never say the plugin lacks "callable resources". In MCP, resources are not callable; tools are.
6. If resources are not surfaced in the session, continue with the EQEmu Oracle tools instead of narrating that the plugin is unavailable.
7. For EQEmu quest-script questions, call an EQEmu Oracle tool before falling back to local quest files unless the user explicitly asked otherwise.
8. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
9. Preserve quest API code blocks so methods, events, and constants are easy to copy.
10. Include the language, categories, related docs, and provenance in the answer.
11. If the task is about script placement, script precedence, or which quest file is active, search the plugin docs context first and reason from the quest loading hierarchy before suggesting code changes.
12. For NPC script work, remember:
   - ID-based filenames beat name-based filenames within the same scope.
   - Lua only takes precedence over Perl for the same exact basename.
   - `quests/global/<npc_id|npc_name>.[ext]` is part of normal selection, while `global_player.[ext]` and `global_npc.[ext]` are overlay scripts that still run alongside the selected script.
13. If the user mentions plugins, treat `/plugins` as a Perl-only feature on this server and prefer it for reusable Perl helpers instead of duplicating logic across Perl quest files.
14. If a Perl script uses the `plugin::` prefix before a function call, treat that as evidence the implementation belongs in a global Perl plugin script under `/plugins`.
15. When repository examples would help script work, search `quests` for quest script examples and `plugins` for Perl plugin examples. Exact example-file hits can be opened with `get_eqemu_example_file`.
