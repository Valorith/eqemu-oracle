# EQEmu Oracle

`EQEmu Oracle` is a Codex plugin project that gives Codex deterministic access to core EQEmu reference material without relying on ad hoc web search.

It stages three primary knowledge domains locally:

- EQEmu quest scripting API for Perl and Lua
- EQEmu MySQL schema
- Official EQEmu documentation

It also supports extension overlays so you can add or override data that exists only in your own server fork, private tooling, or local documentation set.

## What This Plugin Does

The plugin runs a local stdio MCP server and serves EQEmu context from versioned snapshots stored in this repository.

Normal lookups are deterministic:

- committed upstream snapshots live in `plugins/eqemu-oracle/data/base/`
- merged effective records live in `plugins/eqemu-oracle/data/merged/`
- a local search index is built in `plugins/eqemu-oracle/cache/`
- docs are additionally split into section-level search records for better retrieval

Two overlay roots can change the effective data:

- `plugins/eqemu-oracle/extensions/`: repo-tracked shared overlays
- `plugins/eqemu-oracle/local-extensions/`: machine-local untracked overlays

Effective merge precedence is:

1. base upstream snapshot
2. repo extension
3. local extension

That means a local extension can override both the upstream data and a repo-tracked extension without changing committed plugin data.

## Current Upstream Sources

- Quest API: [spire.eqemu.dev Quest API definitions](https://spire.eqemu.dev/quest-api-explorer)
- Quest API provenance: [Valorith/spire](https://github.com/Valorith/spire)
- Schema and docs source repo: [EQEmu/eqemu-docs-v2](https://github.com/EQEmu/eqemu-docs-v2)
- Human-facing schema/docs site: [docs.eqemu.dev](https://docs.eqemu.dev/)

## Repository Layout

- `plugins/eqemu-oracle/`: the actual Codex plugin
- `plugins/eqemu-oracle/.codex-plugin/plugin.json`: plugin metadata
- `plugins/eqemu-oracle/.mcp.json`: local MCP server wiring
- `plugins/eqemu-oracle/scripts/`: CLI and MCP runtime
- `plugins/eqemu-oracle/data/`: committed staged datasets
- `plugins/eqemu-oracle/extensions/`: shared overlay files
- `plugins/eqemu-oracle/local-extensions/`: local-only overlay files
- `plugins/eqemu-oracle/tests/`: unit and runtime smoke tests
- `.github/workflows/refresh-plugin-data.yml`: scheduled/manual refresh automation
- `.agents/plugins/marketplace.json`: local plugin marketplace entry for Codex

## Getting Started

1. Open the repo in Codex.
2. Ensure Python is available on your machine.
3. Load the local plugin from the marketplace entry in `.agents/plugins/marketplace.json`.
4. The plugin MCP server is wired through `plugins/eqemu-oracle/.mcp.json` and starts through:

```powershell
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py mcp-serve
```

## Refreshing Upstream Data

Use the local CLI to rebuild staged data from upstream sources.

Refresh and rewrite the committed snapshots:

```powershell
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py refresh --scope all --mode committed
```

Refresh into a local untracked overlay without replacing committed data:

```powershell
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py refresh --scope all --mode overlay
```

Rebuild merged data after changing only extension files:

```powershell
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py rebuild-extensions --scope all --mode committed
```

Use `--mode overlay` with `rebuild-extensions` if you want the rebuild to target the local overlay instead of the committed merged snapshot.

Update the plugin code from its Git remote and rebuild the committed merged dataset:

```powershell
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py update-plugin
```

## Extension Overlays

The extension system is the supported way to add EQEmu knowledge that does not exist in upstream EQEmu sources.

Common use cases:

- custom quest API methods or events from a private fork
- custom database tables or columns on your local server
- internal docs pages, aliases, or overrides for project-specific workflows

### Where To Put Extensions

- Shared with the repo: `plugins/eqemu-oracle/extensions/`
- Only on your machine: `plugins/eqemu-oracle/local-extensions/`

Both roots use the same format. Domain-specific readmes live here:

- `plugins/eqemu-oracle/extensions/quest-api/README.md`
- `plugins/eqemu-oracle/extensions/schema/README.md`
- `plugins/eqemu-oracle/extensions/docs/README.md`

### Merge Rules

Each extension record must declare a stable `id` matching the target domain.

Supported per-record `mode` values:

- `override`: replace conflicting fields from the base record
- `augment`: merge into the base record and append unique list values
- `disable`: remove the record from the effective merged dataset

If `mode` is omitted:

- existing record id: defaults to `override`
- new record id: defaults to `augment`

Each merged record keeps provenance so the MCP layer can explain whether the effective result came from base data, a repo extension, or a local extension.

### Extension Example

Schema overlay that adds a custom local-only table:

```json
{
  "tables": [
    {
      "id": "my_custom_table",
      "table": "my_custom_table",
      "title": "my_custom_table",
      "category": "custom",
      "columns": [
        {
          "name": "id",
          "data_type": "int",
          "description": "Primary key"
        }
      ],
      "relationships": [],
      "mode": "augment"
    }
  ]
}
```

After adding the file, rebuild merged data:

```powershell
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py rebuild-extensions --scope schema --mode committed
```

## MCP Surface

The plugin exposes deterministic lookup tools through its local MCP server:

- `search_eqemu_context`
- `get_quest_api_entry`
- `get_db_table`
- `get_doc_page`
- `explain_eqemu_provenance`
- `refresh_eqemu_oracle`
- `rebuild_eqemu_extensions`
- `update_eqemu_oracle_plugin`

Lookup tools now also include a presentation layer for user-facing answers:

- quest API entries return a consistent method/event/constant template with copyable signature blocks
- schema entries return a consistent table template with a copyable SQL-style column outline
- docs pages return a consistent page summary template
- search results return a compact, repeatable result list template

It also exposes read resources for staged indexes and direct record navigation:

- `eqemu://manifest`
- `eqemu://indexes/quest-api`
- `eqemu://indexes/schema`
- `eqemu://indexes/docs`

## Testing

Run the current test suite with:

```powershell
python -m unittest discover -s .\plugins\eqemu-oracle\tests
```

Useful validation commands:

```powershell
python -m py_compile .\plugins\eqemu-oracle\scripts\eqemu_oracle.py .\plugins\eqemu-oracle\scripts\eqemu_oracle\*.py
python .\plugins\eqemu-oracle\scripts\eqemu_oracle.py refresh --scope all --mode overlay
```

## Status

The plugin is scaffolded and functional for local development:

- staged upstream ingest is implemented
- extension overlays are implemented
- merged search and deterministic MCP lookup are implemented
- docs are indexed at page and section granularity
- query expansion and EQEmu-specific aliases improve search recall
- refresh automation is wired through GitHub Actions
