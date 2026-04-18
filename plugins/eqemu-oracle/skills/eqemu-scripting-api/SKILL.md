---
name: eqemu-scripting-api
description: Look up EQEmu Perl and Lua quest API methods, events, and constants through EQEmu Oracle.
---

# EQEmu Scripting API

1. If the language, kind, and symbol are known, call `get_quest_api_entry`.
2. Otherwise call `search_eqemu_context` with `domains=["quest-api"]`.
3. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
4. Preserve quest API signature code blocks so methods, events, and constants are easy to copy.
5. Include the language, signature, categories, related docs, and provenance in the answer.
