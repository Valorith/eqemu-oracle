---
name: eqemu-scripting-api
description: Look up EQEmu Perl and Lua quest API methods, events, and constants through EQEmu Oracle.
---

# EQEmu Scripting API

1. If the language, kind, and symbol are known, call `get_quest_api_entry`.
2. If a method has multiple signatures or the user asks which overloads exist, call `get_quest_api_overloads`.
3. If the user asks for a broad topic, family, or "what options are available", call `summarize_quest_api_topic`.
4. Otherwise call `search_eqemu_context` with `domains=["quest-api"]`.
4. Treat the plugin tools as the normal access path.
5. Never say the plugin lacks "callable resources". In MCP, resources are not callable; tools are.
6. If MCP tools are not surfaced in the session, do not narrate a discovery failure. From the installed plugin root, use the CLI fallback for the same tool handler, for example `py -3 scripts\eqemu_oracle.py tool get_quest_api_entry --args '{"language":"perl","kind":"method","name":"say"}'`.
7. For EQEmu quest-script questions, call an EQEmu Oracle tool before falling back to local quest files unless the user explicitly asked otherwise.
8. Prefer the plugin-provided `presentation.markdown` and `copy_blocks` when answering the user.
9. Preserve quest API code blocks so methods, events, and constants are easy to copy.
10. Include the language, categories, related docs, and provenance in the answer.
11. If the task is about script placement, script precedence, or which quest file is active, search the plugin docs context first and reason from the quest loading hierarchy before suggesting code changes.
12. For NPC script work, remember:
   - ID-based filenames beat name-based filenames within the same scope.
   - Lua only takes precedence over Perl for the same exact basename.
   - `quests/global/<npc_id|npc_name>.[ext]` is part of normal selection, while `global_player.[ext]` and `global_npc.[ext]` are overlay scripts that still run alongside the selected script.
13. If the user mentions plugins, treat `/plugins` as a Perl-only feature on this server and prefer it for reusable Perl helpers instead of duplicating logic across Perl quest files.
14. If a Perl script uses the `plugin::` prefix before a function call, treat that as evidence the implementation belongs in a global Perl plugin script under `/plugins`.
15. When repository examples would help script work, search `quests` for quest script examples and `plugins` for Perl plugin examples. Exact example-file hits can be opened with `get_eqemu_example_file`.
16. For work on active server quest/plugin files, call `list_eqemu_server_ftp_profiles` early to discover configured FTP/FTPS profiles when no local workspace path is provided. If no profile exists, use `get_eqemu_server_ftp_onboarding`; never ask the user to paste FTP credentials into chat. Inspect the profile's `read_only` value before considering any FTP write action.
17. Before remote script work, call `map_eqemu_server_ftp_layout` with the narrowest useful scope unless the user supplied an exact remote path and the current task already has a fresh map/list result for that path. Use `scope="zone"` with `zone="<short_name>"` for zone work, `scope="plugins"` for plugin helpers, `scope="global"` for global quest scripts, and `scope="global-items"` or `scope="global-spells"` for item/spell scripts.
18. Use safe FTP tools for remote script review: `test_eqemu_server_ftp_connection`, `map_eqemu_server_ftp_layout`, `list_eqemu_server_ftp_files`, and `stage_eqemu_server_ftp_file`. Prefer mapping the relevant subtree before staging broad remote areas, then stage the active remote file before suggesting server-specific edits.
19. Interpret `map_eqemu_server_ftp_layout` results with the live server layout in mind: `/plugins` holds top-level Perl plugin scripts, `/quests` holds zone folders plus `/quests/global`, `/quests/global/items`, and `/quests/global/spells`, and `/logs` may include server runtime logs plus crash/log subfolders; NPC scripts may be named by NPC name or numeric NPC type id; if `.lua` and `.pl` share the same basename in the same script directory, Lua is active and Perl is shadowed.
20. Treat `trust_eqemu_server_ftp_certificate` as a user-commanded local trust change only. Preview the presented fingerprint first, then only confirm with exact `confirm_sha256` after explicit instruction. Pinned FTPS is preferred over disabling TLS verification for self-signed or hostname-mismatched certificates.
21. Treat `set_eqemu_server_ftp_read_only_mode` as a user-commanded mode change only. Never call it proactively, never infer consent from a broader task, and never disable read-only mode unless the user explicitly instructs that exact mode change.
22. Treat remote upload, delete, and undo as high risk. `upload_eqemu_server_ftp_file` requires explicit user approval plus exact `confirm_remote_path`; creating a new remote file also requires explicit `allow_create=true`. `delete_eqemu_server_ftp_file` requires explicit user approval plus exact `confirm_remote_path` and exact `confirm_remote_sha256` from preview. Use `list_eqemu_server_ftp_write_history` and `preview_eqemu_server_ftp_undo` for undo review. `undo_eqemu_server_ftp_write` requires explicit approval plus exact `confirm_operation_id`. Upload, delete, and undo are blocked while the profile's `read_only` mode is true.
23. Upload and delete create restore points and return write-history operation ids. Undo can restore overwritten/deleted files or remove a file created by guarded upload. Remote rename, chmod, and directory-removal actions are not available.
24. Use `garbage_collect_eqemu_server_ftp_write_history` to preview local undo retention cleanup if write history reports old or orphaned restore points. Applying GC requires explicit approval with `confirm_write=true`; it only deletes local undo backups and never changes remote server files. GC apply is blocked while the profile's `read_only` mode is true.
