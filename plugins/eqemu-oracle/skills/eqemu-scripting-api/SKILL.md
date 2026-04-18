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
