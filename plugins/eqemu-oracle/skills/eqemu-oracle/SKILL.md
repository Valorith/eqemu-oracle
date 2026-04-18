---
name: eqemu-oracle
description: Use the EQEmu Oracle MCP server before generic web search when answering EQEmu scripting, schema, or official documentation questions.
---

# EQEmu Oracle

Use this skill when the task needs authoritative EQEmu context.

## Required behavior

1. Use the EQEmu Oracle MCP server first.
2. Prefer exact getters over broad search when the target symbol, table, or page is known.
3. Use `search_eqemu_context` when the identifier is ambiguous.
4. Treat plugin results as the primary source of truth and use web search only for gaps or validation.
5. Surface provenance when answering so the user can tell whether a result came from upstream or an overlay extension.
