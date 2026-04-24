<img width="779" height="912" alt="EQEmu Oracle logo" src="https://github.com/user-attachments/assets/3fb7197b-bbb3-45a5-acf3-1ac0a50043e5" />

<p align="center">

  <a href="https://github.com/Valorith/eqemu-oracle/actions/workflows/refresh-plugin-data.yml"><img src="https://github.com/Valorith/eqemu-oracle/actions/workflows/refresh-plugin-data.yml/badge.svg" alt="Refresh Data"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/actions/workflows/create-release.yml"><img src="https://github.com/Valorith/eqemu-oracle/actions/workflows/create-release.yml/badge.svg" alt="Create Release"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/graphs/contributors"><img src="https://img.shields.io/github/contributors/Valorith/eqemu-oracle" alt="Contributors"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/releases"><img src="https://img.shields.io/github/v/release/Valorith/eqemu-oracle" alt="Latest Release"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/releases"><img src="https://img.shields.io/github/release-date/Valorith/eqemu-oracle" alt="Release Date"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/pulls?q=is%3Apr+is%3Aclosed"><img src="https://img.shields.io/github/issues-pr-closed/Valorith/eqemu-oracle" alt="Closed Pull Requests"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/commits/main"><img src="https://img.shields.io/github/last-commit/Valorith/eqemu-oracle" alt="Last Commit"></a>
  <img src="assets/codex_dark_badge.svg" alt="Codex" width="22">
  <a href="https://github.com/Valorith/eqemu-oracle"><img src="https://img.shields.io/badge/Codex-Plugin-5A7D2B" alt="Codex Plugin"></a>
</p>

# EQEmu Oracle

`EQEmu Oracle` is a <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex plugin that helps you ask EQEmu questions in plain language and get grounded answers from local EQEmu reference data.

It is built for people who want fast help with EQEmu scripting, database tables, and documentation without digging through multiple sites or relying on generic AI guesses.

This plugin is specifically designed to work with the OpenAI ChatGPT <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex App: https://chatgpt.com/codex

## What It Helps With

Once installed, the plugin can help <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex:

- explain Perl and Lua quest API methods, events, and constants
- look up EQEmu database tables, columns, and relationships
- find and summarize official EQEmu documentation pages
- search across those sources from one prompt
- use your own local server-specific extensions if you want custom data later

## Why Use It

- Local and predictable: answers come from staged EQEmu data in this repository
- Better than ad hoc search: <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex can pull from a purpose-built EQEmu source instead of guessing
- Friendly workflow: ask normal questions instead of memorizing where the information lives
- Expandable: you can add your own schema, docs, or quest API overlays for private server changes

## Quick Start

If you only care about getting it running, follow these steps:

1. Download the latest release zip from [Releases](https://github.com/Valorith/eqemu-oracle/releases) and extract it on your computer.
2. Install Python 3 if you do not already have it.
3. Run the install script from the extracted release folder.
   
   <img src="assets/windows.svg" alt="Windows" width="18" /> Windows:

   ```powershell
   .\install.cmd
   ```

   <img src="assets/apple_dark.svg" alt="macOS" width="16" /> macOS/Linux:

   ```sh
   ./install.sh
   ```

   On macOS, `install.sh` is meant to be run from Terminal. If you double-click it in Finder, macOS may open it in a text editor instead of executing it. For a double-clickable macOS launcher, use `install.command`.
   <img width="717" height="163" alt="image" src="https://github.com/user-attachments/assets/d60ea500-8b48-4c0f-a0c4-3e4befc73033" />


4. If <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex was already open, restart it once.
5. Go to the Plugins window within <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex.
   <img width="418" height="175" alt="image" src="https://github.com/user-attachments/assets/eb7f0658-587e-4500-b7c0-617c6280cf5d" />

6. Enable the `EQEmu Oracle` plugin.
   <img width="814" height="277" alt="image" src="https://github.com/user-attachments/assets/b5c198cf-a593-44e2-a0a9-4081b0ace53b" />

7. Ask <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex an EQEmu question from any project thread while invoking the plugin with `@EQEmu Oracle`.
   <img width="747" height="439" alt="image" src="https://github.com/user-attachments/assets/23aa3e1e-8966-4ea9-82c5-3fc5cfa3aeb0" />
   
   <img width="756" height="133" alt="image" src="https://github.com/user-attachments/assets/78ac79e9-c8fe-4f48-abe4-c31dcd4118d3" />


## Development

If you are developing the plugin from a local checkout and want your latest changes to be available globally, rerun the install script after you edit the plugin files.

If you specifically need the lower-level Python entrypoint, use:

<img src="assets/windows.svg" alt="Windows" width="18" /> Windows:

```powershell
py -3 plugins/eqemu-oracle/scripts/eqemu_oracle.py install
```

<img src="assets/apple_dark.svg" alt="macOS" width="16" /> macOS/Linux:

```sh
python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py install
```

The install script preserves local overrides in `local-extensions/` and `config/sources.local.toml` when refreshing an existing install.

### Repo-Local Development Mode

For development on this repository itself, open the top-level repository folder in <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex.

Do not open only `plugins/eqemu-oracle/`. The repo-local marketplace and MCP wiring expect the full repository layout to stay intact.

When this repository is opened as a <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex project, the repo-local plugin should still appear in that project's Plugins UI without any separate marketplace import step.

## First Use

Once the plugin is installed, try prompts like these:

- `Use EQEmu Oracle to explain quest::say`
- `Use EQEmu Oracle to find the schema for npc_types`
- `Use EQEmu Oracle to find the docs page for Perl quest events`
- `Use EQEmu Oracle to compare spell data tables related to NPC casting`

You do not need to manually start the plugin in normal use. <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex handles that through the included MCP configuration.

## What You Can Ask

The plugin is most useful for three kinds of questions:

### Quest Scripting Help

Ask what a method does, what arguments it takes, when an event fires, or how to use a constant in Perl or Lua quests.

Examples:

- `Use EQEmu Oracle to explain quest::setglobal`
- `Use EQEmu Oracle to show me how EVENT_SAY works in Perl`
- `Use EQEmu Oracle to explain the difference between a quest method and a plugin call`

### Database Help

Ask about EQEmu tables, columns, and related records.

Examples:

- `Use EQEmu Oracle to explain the npc_types table`
- `Use EQEmu Oracle to find tables related to loot`
- `Use EQEmu Oracle to show the columns for spawn2`

### Documentation Lookup

Ask <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex to find official docs and summarize them.

Examples:

- `Use EQEmu Oracle to find the documentation for bots`
- `Use EQEmu Oracle to summarize the task system docs`
- `Use EQEmu Oracle to find the EQEmu docs page about server configuration`

## How It Works

At a high level, the plugin gives <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex a local EQEmu reference source made from:

- quest scripting API data
- EQEmu schema data
- official EQEmu documentation snapshots

That means answers are based on the staged data in this repository instead of loose web browsing.

## Troubleshooting

### The Plugin Does Not Show Up In <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex

First, run the install script from the repository root:

```powershell
.\install.cmd
```

Then:

1. Restart <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex if it was already open.
2. Check whether `EQEmu Oracle` appears in the Plugins UI.
3. Install or enable `EQEmu Oracle` if needed.

If <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex still does not show the plugin after a restart, rerun the install script.

### Python Is Not Found

Install Python 3, then reopen the terminal and re-run the version check.

### <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex Sees The Plugin But It Does Not Answer

Run this manual check from the repository root:

<img src="assets/windows.svg" alt="Windows" width="18" /> Windows:

```powershell
py -3 plugins/eqemu-oracle/scripts/eqemu_oracle.py mcp-serve
```

<img src="assets/apple_dark.svg" alt="macOS" width="16" /> macOS:

```sh
python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py mcp-serve
```

If the command starts cleanly, the runtime is available and <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex should be able to use it.

### You Downloaded Only The Plugin Folder

Download or clone the full repository instead. The install script expects the full repository root, not just `plugins/eqemu-oracle/`.

## Optional: Add Your Own Server Knowledge

If your server has custom tables, docs, quest API behavior, or preferred quest/plugin example repositories, you can add that on top of the built-in EQEmu data.

Use:

- `plugins/eqemu-oracle/extensions/` for shared repo-tracked additions
- `plugins/eqemu-oracle/local-extensions/` for machine-local additions you do not want in git

For local additions, edit the installer-created `local.json` scaffold files or copy a `_example.json` file to a new filename and edit that copy. The plugin rebuilds local edits into its ignored overlay cache when it serves extension-backed results, and git ignores the local JSON files so private server resources are not uploaded to the repository or release bundles.

This is optional. Most users can ignore it until they need custom behavior.

## Optional: Point To Different Sources

If you want to use a fork, mirror, or different upstream source set:

1. Copy `plugins/eqemu-oracle/config/sources.toml`
2. Save it as `plugins/eqemu-oracle/config/sources.local.toml`
3. Change only the values you need

The local file is meant for personal overrides.

## Project Layout

If you want to know where things live:

- `.agents/plugins/marketplace.json`: local marketplace entry for <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex
- `plugins/eqemu-oracle/`: the actual plugin
- `plugins/eqemu-oracle/.codex-plugin/plugin.json`: plugin metadata
- `plugins/eqemu-oracle/.mcp.json`: local MCP server wiring
- `plugins/eqemu-oracle/scripts/`: runtime and CLI
- `plugins/eqemu-oracle/data/`: staged EQEmu data
- `plugins/eqemu-oracle/extensions/`: shared extension files
- `plugins/eqemu-oracle/local-extensions/`: local-only extension files

## In One Sentence

If you use <img src="assets/codex_dark_badge.svg" alt="Codex" width="16" /> Codex for EQEmu work, `EQEmu Oracle` gives it a clean, local source for scripting help, schema lookup, and documentation answers.
