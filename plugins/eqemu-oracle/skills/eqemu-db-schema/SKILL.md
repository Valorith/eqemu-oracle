---
name: eqemu-db-schema
description: Look up EQEmu MySQL tables, columns, and relationships through EQEmu Oracle.
---

# EQEmu DB Schema

1. If the table name is known, call `get_db_table`.
2. Otherwise call `search_eqemu_context` with `domains=["schema"]`.
3. Include columns, relationships, docs URL, and provenance in the answer.
