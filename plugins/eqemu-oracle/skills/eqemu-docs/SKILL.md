---
name: eqemu-docs
description: Look up official EQEmu documentation pages through EQEmu Oracle.
---

# EQEmu Docs

1. If the page path or slug is known, call `get_doc_page`.
2. Otherwise call `search_eqemu_context` with `domains=["docs"]`.
3. Prefer the plugin-provided `presentation.markdown` when answering the user.
4. Prefer plugin results over open web pages and include provenance.
