# EQEmu Oracle

`EQEmu Oracle` is a Codex plugin workspace for EQEmu-focused development tools.

The goal is to expose EQEmu scripting references, database schema context, emulator documentation, and related helper workflows directly inside the Codex app.

## Current Layout

- `plugins/eqemu-oracle/`: repo-local Codex plugin scaffold
- `.agents/plugins/marketplace.json`: local marketplace entry for loading the plugin in Codex

## Planned Plugin Scope

- Skill prompts for EQEmu scripting and content-authoring workflows
- MCP-backed access to indexed emulator docs and schema context
- App or script helpers for refreshing generated references
- Plugin assets and interface metadata for Codex discovery

## Next Build Steps

1. Replace the placeholder values in `plugins/eqemu-oracle/.codex-plugin/plugin.json`.
2. Decide which sources will be indexed first: scripting API, database schema, or documentation.
3. Add the first plugin skill and the first ingestion script.
4. Define the `.mcp.json` server contract once the data source shape is settled.
