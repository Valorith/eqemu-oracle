# EQEmu Oracle Plugin

`eqemu-oracle` is a Codex plugin that serves deterministic EQEmu context through a built-in stdio MCP server.

## Scope

- Perl and Lua quest API lookup
- EQEmu schema lookup
- Official EQEmu documentation lookup
- Shared and local extension overlays
- Refresh and merge tooling for staged data
- Section-level docs indexing and synonym-aware search
- Optional FTP/FTPS staging for remote EQEmu server files, with local credential storage and guarded upload

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

When you are using private `local-extensions/` files in a git checkout, rebuild into the ignored overlay instead:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py rebuild-extensions --scope all --mode overlay
```

Update the plugin from its Git remote and rebuild committed merged data:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py update-plugin
```

Return to your previous branch after updating from a different branch:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py update-plugin --branch my-branch --restore-branch
```

`<python-launcher>` means `python3` on macOS/Linux and `py -3` (or `python`) on Windows. Codex starts the installed MCP server with the resolved Python executable and `scripts/eqemu_oracle.py mcp-serve`, avoiding shell-wrapper startup differences. Python 3.11+ is still preferred, but the checked-in `sources.toml` format also works on older Python 3 versions without installing extra parser dependencies.

Install or refresh the global home-local copy:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py install
```

On Codex Desktop this installs into the stable local marketplace under `~/.codex/local-marketplaces/user-local` and only falls back to the legacy home-local plugin path when Codex is unavailable.
When Codex is present, the installer enables the plugin in `~/.codex/config.toml`, points the direct MCP server at the editable catalog checkout, and refreshes the Codex plugin-loader cache copy from that checkout. Current Codex Desktop builds may load the plugin skills without exposing local plugin MCP tools through `tool_search`; in that case use the CLI fallback shown below.

Set up a remote EQEmu server FTP/FTPS profile:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote setup
```

The setup flow prompts locally and hides the password input. Profile metadata and staged files are saved under the local EQEmu Oracle user-data directory, outside this repository. Passwords are stored through the OS credential store when available, or Windows DPAPI on Windows. Do not paste FTP passwords into chat.

For FTPS servers with self-signed certificates or certificates that do not match the FTP host, prefer a pinned certificate over disabling TLS verification. Preview the presented certificate fingerprint, then store the pin only after exact confirmation:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote trust-cert live
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote trust-cert live --confirm-trust --confirm-sha256 <sha256-fingerprint-from-preview>
```

Pinned FTPS keeps the connection encrypted and refuses to send credentials if the server presents a different certificate later.

Remote profiles also have a persisted read-only mode. Preview a mode change first, then apply it with exact confirmation fields. Disabling read-only mode requires explicit user instruction for that specific task. Re-enabling read-only mode is allowed as automatic safety cleanup after an approved write task where the agent disabled it for that same task:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote read-only live --enable
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote read-only live --enable --confirm-mode-change --confirm-profile live --confirm-read-only-mode read-only
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote read-only live --disable --confirm-mode-change --confirm-profile live --confirm-read-only-mode read-write
```

When read-only mode is enabled, upload, confirmed undo, and undo garbage-collection apply are blocked in the shared FTP implementation. Listing, testing, staging, history review, and undo preview remain available.

Remote file workflow:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote profiles
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote test live
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote map live --scope overview
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote map live --scope zone --zone qeynos
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote list live --remote-path quests
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote stage live quests/qeynos/Guard_Beren.pl
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote history live
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote gc live
```

`remote map` is a read-only, bounded EQEmu layout mapper. Use it on demand before staging or editing remote files so the agent works from the actual server inventory instead of guessed paths. The mapper supports scoped discovery: `overview`, `quests`, `zone`, `plugins`, `logs`, `binaries`, `global`, `global-items`, and `global-spells`. Broad `quests` and `binaries` scopes inventory folders first; use `zone`, `global*`, or an explicit `remote_path` for file-level discovery. It classifies the expected remote structure:

- `/binaries` contains binary package subfolders.
- `/logs/crashes` contains server crash report text files.
- `/logs/zone` contains zone crash report text files and may contain zone runtime log files.
- `/logs/*.log` may contain top-level server runtime logs.
- `/plugins` contains top-level Perl plugin scripts.
- `/quests` contains one folder per zone plus `/quests/global`.
- `/quests/global/items` and `/quests/global/spells` contain global item and spell scripts.

The mapper also returns available-file buckets with sample paths, marks NPC quest scripts that use numeric NPC type ids versus script names, and reports Lua-over-Perl priority conflicts when `.lua` and `.pl` files share the same basename in the same script directory.

Uploads are intentionally a two-step operation. A first upload call returns the exact confirmation arguments, and the confirmed call requires `--confirm-write` plus `--confirm-remote-path <resolved remote path>`. Creating a new remote file additionally requires `--allow-create`; otherwise a missing target is treated as a mistake and no write is attempted. Upload is blocked while the profile is in read-only mode. Every upload creates a local restore point before writing, verifies the remote file after upload, and records a write-history operation id.

Undo is also guarded:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote undo-preview live <operation-id>
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote undo live <operation-id> --confirm-write --confirm-operation-id <operation-id>
```

Undo refuses to run if the current remote file no longer matches the recorded post-write hash, unless that changed remote state is explicitly allowed. Confirmed undo is blocked while the profile is in read-only mode. Undo itself creates a new restore point first, so an undo can be reversed. Undo can also remove a file created by a guarded upload or restore a file removed by a guarded delete. The local write-history retention defaults to the most recent 25 remote writes per profile or 250 MB of restore-point data. Upload and undo automatically prune local restore points beyond that policy as new write-history entries are created.

Delete is guarded by preview and exact confirmation:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote delete live quests/global/textFile.txt
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote delete live quests/global/textFile.txt --confirm-delete --confirm-remote-path /quests/global/textFile.txt --confirm-remote-sha256 <sha256-from-preview>
```

Confirmed delete is blocked while read-only mode is enabled. The confirmed delete re-downloads the remote file, requires the exact current SHA-256 from preview, saves a local restore point, deletes the file, verifies the path is gone, and records a write-history operation id for undo.

Garbage collection can also be previewed or applied manually:

```sh
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote gc live
<python-launcher> plugins/eqemu-oracle/scripts/eqemu_oracle.py remote gc live --apply --confirm-write
```

The GC command never touches the remote server. It deletes only local restore-point files that are beyond the retention policy or orphaned under that profile's local backup directory. Applying GC is blocked while the profile is in read-only mode. Rename, chmod, and directory-removal operations are not exposed.

Agent workflow for remote server files:

1. Call `list_eqemu_server_ftp_profiles` before assuming FTP/FTPS is configured.
2. If no profiles exist, call `get_eqemu_server_ftp_onboarding`.
3. If one profile exists, use it for safe list/stage work unless the user names another profile.
4. If multiple profiles exist, ask which profile to use before staging or writing.
5. Use `map_eqemu_server_ftp_layout` before staging broad remote areas or when the active file location is ambiguous. Prefer the narrowest useful scope, for example `zone` for one zone, `plugins` for helper scripts, `logs` for crash triage, and `global-items` or `global-spells` for item/spell script work.
6. If the Nexus CLI skill/helper is setup and available, use it only when it is value-added to the current task, such as checking the Webhook Inbox for script errors, failed webhooks, or crash-report payloads that may identify the affected script.
7. For script-error fixes on a configured server, map/list the FTP layout, optionally check value-added Nexus Webhook Inbox evidence, stage the active affected script locally, edit the local staged copy, then ask the user to review and explicitly approve upload before overwriting the remote file.
8. After a script error fix is uploaded through FTP, if Nexus is setup and available, ask whether the user wants a Test Manager change record created. Only create it after explicit approval, and keep the Change Description concise and tester-facing: player-visible issue, what changed, and what should be tested.
9. Treat `read_only` as authoritative. If the requested task requires writing while read-only mode is on, tell the user and ask for explicit permission to exit read-only mode for that task. Re-enable read-only mode automatically as cleanup after the approved FTP write task finishes, and report the final state.
10. Use upload only after explicit approval and only when read-only mode is off, then report the returned operation id.
11. Use write history and undo preview before any undo, then run undo only after explicit approval and only when read-only mode is off.
12. Use `garbage_collect_eqemu_server_ftp_write_history` to preview local undo cleanup when history reports retention pressure. Apply GC only after explicit approval with `confirm_write=true` and only when read-only mode is off; it never modifies remote server files.

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
- Quest script example source extensions use a `sources` array
- Perl plugin example source extensions use a `sources` array

For `quests` and `plugins`, a local source with the same `context_key` as a repo-level source replaces the repo-level source for that context. Use a different `context_key` to add a supplemental source.

Private local extension records are rebuilt into the ignored overlay cache for local use. Committed rebuilds in a git checkout refuse to run while active local extension records are present, so private source URLs are not accidentally written into tracked `data/merged` files.

See:

- `extensions/quest-api/README.md`
- `extensions/schema/README.md`
- `extensions/docs/README.md`
- `extensions/quests/README.md`
- `extensions/plugins/README.md`

## MCP Tools

- `search_eqemu_context`
- `get_quest_api_entry`
- `get_quest_api_overloads`
- `summarize_quest_api_topic`
- `get_db_table`
- `explain_db_relationships`
- `get_doc_page`
- `get_eqemu_example_file`
- `explain_eqemu_provenance`
- `refresh_eqemu_oracle`
- `rebuild_eqemu_extensions`
- `prune_stale_schema_extensions`
- `update_eqemu_oracle_plugin`
- `get_eqemu_server_ftp_onboarding`
- `list_eqemu_server_ftp_profiles`
- `test_eqemu_server_ftp_connection`
- `trust_eqemu_server_ftp_certificate`
- `list_eqemu_server_ftp_files`
- `map_eqemu_server_ftp_layout`
- `stage_eqemu_server_ftp_file`
- `upload_eqemu_server_ftp_file`
- `delete_eqemu_server_ftp_file`
- `list_eqemu_server_ftp_write_history`
- `set_eqemu_server_ftp_read_only_mode`
- `garbage_collect_eqemu_server_ftp_write_history`
- `preview_eqemu_server_ftp_undo`
- `undo_eqemu_server_ftp_write`

Getter and search tools also attach `presentation.markdown` and `copy_blocks` so Codex can answer users with a consistent polished format while still keeping the raw structured record available to agents. Quest API events are rendered in a Spire-style copyable code format.
Maintenance tools that can write local plugin data or touch Git state require `confirm_write: true`.
Remote FTP/FTPS map, list, history, preview, and stage tools are safe for agents to use when relevant. `trust_eqemu_server_ftp_certificate` must only be confirmed after explicit user instruction. `set_eqemu_server_ftp_read_only_mode` must only be confirmed to disable read-only mode after explicit user instruction for that task; confirming an enable back to read-only is allowed as automatic safety cleanup after an approved write task. `garbage_collect_eqemu_server_ftp_write_history` is safe to preview, but applying it deletes local undo restore points and requires explicit confirmation. `upload_eqemu_server_ftp_file`, `delete_eqemu_server_ftp_file`, and `undo_eqemu_server_ftp_write` require explicit confirmation and matching confirmation fields, and are blocked while read-only mode is enabled.
`search_eqemu_context` also accepts `prefer_fresh: true` to break ranking ties toward newer staged records.
When `quests` or `plugins` is explicitly searched, configured example sources are indexed into the ignored cache so results can include real quest/plugin files, not just source metadata.

## MCP Resources

The server is tool-first, but it also exposes browseable MCP resources for clients that support them:

- `eqemu://manifest`
- `eqemu://indexes/quest-api`
- `eqemu://indexes/schema`
- `eqemu://indexes/docs`
- `eqemu://indexes/docs-sections`
- `eqemu://indexes/quests`
- `eqemu://indexes/plugins`
- `eqemu://quest-api/{id}`
- `eqemu://schema/table/{table_name}`
- `eqemu://docs/page/{path}`
- `eqemu://quests/source/{id}`
- `eqemu://plugins/source/{id}`
- `eqemu://quests/example/{id}`
- `eqemu://plugins/example/{id}`
- `eqemu://provenance/{domain}/{id}`

If a Codex session does not surface generic MCP resources or local plugin MCP tools, the plugin is still usable through the CLI `tool` command. The CLI command calls the same local handlers as the MCP server and returns the same structured payload.
