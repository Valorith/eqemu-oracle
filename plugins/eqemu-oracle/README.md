# EQEmu Oracle Plugin

`eqemu-oracle` is a Codex plugin that serves deterministic EQEmu context through a built-in stdio MCP server.

## Scope

- Perl and Lua quest API lookup
- EQEmu schema lookup
- Official EQEmu documentation lookup
- Shared and local extension overlays
- Refresh and merge tooling for staged data
- Section-level docs indexing and synonym-aware search

## Key Files And Folders

- `.codex-plugin/plugin.json`: plugin metadata and Codex interface settings
- `.mcp.json`: MCP server wiring used by Codex
- `scripts/eqemu_oracle.py`: CLI entrypoint for refresh, rebuild, and MCP serve
- `scripts/eqemu_oracle/`: runtime package
- `data/base/`: normalized upstream snapshots
- `data/merged/`: effective records after overlay merge
- `extensions/`: repo-tracked overlays
- `local-extensions/`: machine-local overlays ignored by git
- `config/sources.toml`: tracked source defaults
- `config/sources.local.toml`: optional local override, ignored by git
- `tests/`: unit and smoke tests

## Installation

For first-time setup in Codex, start with the repository root README:

- `../../README.md`

That document covers:

- downloading and extracting the release zip
- Windows setup
- macOS setup
- Python verification
- running the global installer
- how the installer registers the global plugin catalog entry
- verifying that Codex can see and use the plugin
- basic troubleshooting

This plugin README focuses on the runtime, data layout, and CLI after installation. The repo-local marketplace remains available for development, but it is not the primary user install path.

## CLI

Run the local MCP server:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py mcp-serve
```

Refresh upstream snapshots and rebuild merged data:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py refresh --scope all --mode committed
```

Refresh into the local untracked overlay:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py refresh --scope all --mode overlay
```

Rebuild merged data from existing snapshots plus overlays:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py rebuild-extensions --scope all --mode committed
```

Update the plugin from its Git remote and rebuild committed merged data:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py update-plugin
```

Return to your previous branch after updating from a different branch:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py update-plugin --branch my-branch --restore-branch
```

`<python-launcher>` means `python3` on macOS/Linux and `py -3` (or `python`) on Windows. Codex itself starts the plugin through `scripts/eqemu_oracle_launcher.cmd`, which bridges that difference for the MCP server startup path. Python 3.11+ is still preferred, but the checked-in `sources.toml` format also works on older Python 3 versions without installing extra parser dependencies.

Install or refresh the global home-local copy:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py install
```

On Codex Desktop this prefers the app-managed catalog under `~/.codex/.tmp/plugins` and only falls back to `~/plugins` plus `~/.agents/plugins/marketplace.json` when the desktop catalog is unavailable.
When Codex is present, the installer enables the plugin in `~/.codex/config.toml` without creating an extra local cache copy.

## Overlay Model

Effective data is built from three layers:

1. upstream base snapshot
2. repo extension
3. local extension

Supported `mode` values on extension records:

- `override`
- `augment`
- `disable`

If an extension record reuses an existing id and no mode is set, it defaults to `override`. If it introduces a new id and no mode is set, it defaults to `augment`.

## Domain Formats

- Quest API extensions use a `records` array
- Schema extensions use a `tables` array
- Docs extensions use a `pages` array

See:

- `extensions/quest-api/README.md`
- `extensions/schema/README.md`
- `extensions/docs/README.md`

## MCP Tools

- `search_eqemu_context`
- `get_quest_api_entry`
- `summarize_quest_api_topic`
- `get_db_table`
- `get_doc_page`
- `explain_eqemu_provenance`
- `refresh_eqemu_oracle`
- `rebuild_eqemu_extensions`
- `prune_stale_schema_extensions`
- `update_eqemu_oracle_plugin`

Getter and search tools also attach `presentation.markdown` and `copy_blocks` so Codex can answer users with a consistent polished format while still keeping the raw structured record available to agents. Quest API events are rendered in a Spire-style copyable code format.
`search_eqemu_context` also accepts `prefer_fresh: true` to break ranking ties toward newer staged records.

## MCP Resources

The server is tool-first, but it also exposes browseable MCP resources for clients that support them:

- `eqemu://manifest`
- `eqemu://indexes/quest-api`
- `eqemu://indexes/schema`
- `eqemu://indexes/docs`
- `eqemu://indexes/docs-sections`
- `eqemu://quest-api/{id}`
- `eqemu://schema/table/{table_name}`
- `eqemu://docs/page/{path}`
- `eqemu://provenance/{domain}/{id}`

If a Codex session does not surface generic MCP resources, the plugin is still usable through its MCP tools. Resource visibility is supplemental, not the primary access path.
