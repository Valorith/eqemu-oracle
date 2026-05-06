---
name: eqemu-docs
description: Look up official EQEmu documentation pages through EQEmu Oracle.
---

# EQEmu Docs

1. If the page path or slug is known, call `get_doc_page`.
2. Otherwise call `search_eqemu_context` with `domains=["docs"]`.
3. If MCP tools are not surfaced in the session, do not narrate a discovery failure. From the installed plugin root, use the CLI fallback for the same tool handler, for example `py -3 scripts\eqemu_oracle.py tool get_doc_page --args '{"path_or_slug":"schema/spawns/spawn2"}'`.
4. Prefer the plugin-provided `presentation.markdown` when answering the user.
5. Prefer plugin results over open web pages and include provenance.
