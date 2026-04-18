# eqemu-oracle Plugin

This directory contains the Codex plugin scaffold for `eqemu-oracle`.

The generated manifest intentionally still contains `[TODO: ...]` placeholders. That keeps the scaffold aligned with the plugin creator workflow while giving us a stable place to start implementation.

## Expected Responsibilities

- Provide Codex-facing metadata through `.codex-plugin/plugin.json`
- Host local MCP wiring in `.mcp.json`
- Host app wiring in `.app.json`
- Store EQEmu-specific skills, scripts, hooks, and assets

## Immediate Follow-Up

1. Replace manifest placeholders with real plugin metadata.
2. Add the first skill under `skills/` for schema and scripting lookups.
3. Add ingestion scripts under `scripts/` for docs and data refresh.
4. Decide whether hooks are needed for local validation or cache refresh workflows.
