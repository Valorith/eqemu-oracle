---
name: eqemu-db-schema
description: Look up EQEmu MySQL tables, columns, and relationships through EQEmu Oracle.
---

# EQEmu DB Schema

1. If the table name is known, call `get_db_table`.
2. Otherwise call `search_eqemu_context` with `domains=["schema"]`.
3. If the user asks how tables connect, joins, inbound references, or surrounding table graph, call `explain_db_relationships`.
4. If MCP tools are not surfaced in the session, do not narrate a discovery failure. From the installed plugin root, use the CLI fallback for the same tool handler, for example `py -3 scripts\eqemu_oracle.py tool get_db_table --args '{"table_name":"spawn2"}'`.
5. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
6. Preserve schema code blocks so the table outline is easy to copy.
7. Include columns, relationships, docs URL, and provenance in the answer.
