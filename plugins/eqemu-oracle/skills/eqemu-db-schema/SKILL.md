---
name: eqemu-db-schema
description: Look up EQEmu MySQL tables, columns, and relationships through EQEmu Oracle.
---

# EQEmu DB Schema

1. If the table name is known, call `get_db_table`.
2. Otherwise call `search_eqemu_context` with `domains=["schema"]`.
3. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
4. Preserve schema code blocks so the table outline is easy to copy.
5. Include columns, relationships, docs URL, and provenance in the answer.
