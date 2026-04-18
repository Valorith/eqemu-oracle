---
name: eqemu-scripting-api
description: Look up EQEmu Perl and Lua quest API methods, events, and constants through EQEmu Oracle.
---

# EQEmu Scripting API

1. If the language, kind, and symbol are known, call `get_quest_api_entry`.
2. If the user asks for a broad topic, family, or "what options are available", call `summarize_quest_api_topic`.
3. Otherwise call `search_eqemu_context` with `domains=["quest-api"]`.
4. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
5. Preserve quest API code blocks so methods, events, and constants are easy to copy.
6. Include the language, categories, related docs, and provenance in the answer.
7. If the task is about script placement, script precedence, or which quest file is active, search the plugin docs context first and reason from the quest loading hierarchy before suggesting code changes.
8. For NPC script work, remember:
   - ID-based filenames beat name-based filenames within the same scope.
   - Lua only takes precedence over Perl for the same exact basename.
   - `quests/global/<npc_id|npc_name>.[ext]` is part of normal selection, while `global_player.[ext]` and `global_npc.[ext]` are overlay scripts that still run alongside the selected script.
9. If the user mentions plugins, treat `/plugins` as a Perl-only feature on this server and prefer it for reusable Perl helpers instead of duplicating logic across Perl quest files.
10. If a Perl script uses the `plugin::` prefix before a function call, treat that as evidence the implementation belongs in a global Perl plugin script under `/plugins`.
