---
name: eqemu-oracle
description: Use the EQEmu Oracle MCP server before generic web search when answering EQEmu scripting, schema, or official documentation questions.
---

# EQEmu Oracle

Use this skill when the task needs authoritative EQEmu context.

## Required behavior

1. Use the EQEmu Oracle MCP server first.
2. Prefer exact getters over broad search when the target symbol, table, or page is known.
3. Use `summarize_quest_api_topic` for broad quest API topic questions such as "aggro", "loot", "task", or "what options are available".
4. Use `search_eqemu_context` when the identifier is ambiguous.
5. Treat plugin results as the primary source of truth and use web search only for gaps or validation.
6. Surface provenance when answering so the user can tell whether a result came from upstream or an overlay extension.
7. If the user asks to update or refresh the plugin itself from Git, use `update_eqemu_oracle_plugin`.
8. When a plugin result includes `presentation.markdown`, prefer that structure for the user-facing answer so API, schema, and docs responses stay consistent.
