---
name: eqemu-db-schema
description: Look up EQEmu MySQL tables, columns, and relationships through EQEmu Oracle.
---

# EQEmu DB Schema

1. If the table name is known, call `get_db_table`.
2. Otherwise call `search_eqemu_context` with `domains=["schema"]`.
3. If the user asks how tables connect, joins, inbound references, or surrounding table graph, call `explain_db_relationships`.
4. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
5. Preserve schema code blocks so the table outline is easy to copy.
6. Include columns, relationships, docs URL, and provenance in the answer.
