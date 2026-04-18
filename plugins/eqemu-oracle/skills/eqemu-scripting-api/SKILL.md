---
name: eqemu-scripting-api
description: Look up EQEmu Perl and Lua quest API methods, events, and constants through EQEmu Oracle.
---

# EQEmu Scripting API

1. If the language, kind, and symbol are known, call `get_quest_api_entry`.
2. Otherwise call `search_eqemu_context` with `domains=["quest-api"]`.
3. Include the language, signature, categories, and provenance in the answer.
