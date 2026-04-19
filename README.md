<img width="779" height="912" alt="EQEmu Oracle logo" src="https://github.com/user-attachments/assets/3fb7197b-bbb3-45a5-acf3-1ac0a50043e5" />

<p align="center">

  <a href="https://github.com/Valorith/eqemu-oracle/actions/workflows/refresh-plugin-data.yml"><img src="https://github.com/Valorith/eqemu-oracle/actions/workflows/refresh-plugin-data.yml/badge.svg" alt="Refresh Data"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/actions/workflows/create-release.yml"><img src="https://github.com/Valorith/eqemu-oracle/actions/workflows/create-release.yml/badge.svg" alt="Create Release"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/graphs/contributors"><img src="https://img.shields.io/github/contributors/Valorith/eqemu-oracle" alt="Contributors"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/releases"><img src="https://img.shields.io/github/v/release/Valorith/eqemu-oracle" alt="Latest Release"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/releases"><img src="https://img.shields.io/github/release-date/Valorith/eqemu-oracle" alt="Release Date"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/pulls?q=is%3Apr+is%3Aclosed"><img src="https://img.shields.io/github/issues-pr-closed/Valorith/eqemu-oracle" alt="Closed Pull Requests"></a>
  <a href="https://github.com/Valorith/eqemu-oracle/commits/main"><img src="https://img.shields.io/github/last-commit/Valorith/eqemu-oracle" alt="Last Commit"></a>
  <a href="https://github.com/Valorith/eqemu-oracle"><img src="https://img.shields.io/badge/Codex-Plugin-5A7D2B" alt="Codex Plugin"></a>
</p>

# EQEmu Oracle

`EQEmu Oracle` is a Codex plugin that helps you ask EQEmu questions in plain language and get grounded answers from local EQEmu reference data.

It is built for people who want fast help with EQEmu scripting, database tables, and documentation without digging through multiple sites or relying on generic AI guesses.

## What It Helps With

Once installed, the plugin can help Codex:

- explain Perl and Lua quest API methods, events, and constants
- look up EQEmu database tables, columns, and relationships
- find and summarize official EQEmu documentation pages
- search across those sources from one prompt
- use your own local server-specific extensions if you want custom data later

## Why Use It

- Local and predictable: answers come from staged EQEmu data in this repository
- Better than ad hoc search: Codex can pull from a purpose-built EQEmu source instead of guessing
- Friendly workflow: ask normal questions instead of memorizing where the information lives
- Expandable: you can add your own schema, docs, or quest API overlays for private server changes

## Quick Start

If you only care about getting it running, follow these steps:

1. Download the latest release zip from [Releases](https://github.com/Valorith/eqemu-oracle/releases) and extract it on your computer.
2. Install Python 3 if you do not already have it.
3. Open the extracted release folder in Codex.
   <img width="364" height="211" alt="image" src="https://github.com/user-attachments/assets/e23c6853-db44-46a4-9f5b-9b432ca1e9e6" />

4. Open the Plugins UI in Codex and check whether `EQEmu Oracle` already appears.
5. If it already appears, install or enable it there.
6. If it does not appear, use the Plugins UI option to load or import a local marketplace file, then select `.agents/plugins/marketplace.json` from the repository root.
7. Install or enable the `EQEmu Oracle` plugin from that marketplace entry.
   <img width="774" height="267" alt="image" src="https://github.com/user-attachments/assets/5f3bcc21-9301-4d29-8f8f-92b39b85d146" />

8. Ask Codex an EQEmu question.

Important: open the repository root, not just `plugins/eqemu-oracle/`. The plugin uses repo-relative paths and will not load correctly if only part of the repo is opened.

## Setup

### 1. Get The Repository

For normal installation, download the latest release zip from [Releases](https://github.com/Valorith/eqemu-oracle/releases), then extract it anywhere convenient on your machine.

The extracted folder will be named something like `eqemu-oracle-vX.Y.Z`.

If you want the live source checkout for development or contribution work, you can clone the repository instead.

Examples:

- Windows: `C:\Users\<you>\Documents\eqemu-oracle-vX.Y.Z`
- macOS: `~/Code/eqemu-oracle-vX.Y.Z`

### 2. Confirm Python

You only need a working Python 3 install.

Windows:

```powershell
py -3 --version
```

If that does not work, try:

```powershell
python --version
```

macOS:

```sh
python3 --version
```

### 3. Open The Repo In Codex

Open the top-level repository folder in Codex.

Do not open only the plugin subfolder. The marketplace entry and MCP wiring expect the full repo layout to stay intact.

### 4. Install The Plugin In Codex

In Codex:

1. Open the Plugins UI and check whether `EQEmu Oracle` is already listed.
2. If it is already listed, install or enable it and skip the marketplace step.
3. If it is not listed, use the Plugins UI option to load or import a local marketplace file.
4. Select `.agents/plugins/marketplace.json` from the repository root.
5. Find `EQEmu Oracle`.
6. Install or enable it.

After that, Codex should start the local plugin automatically when needed.

## First Use

Once the plugin is installed, try prompts like these:

- `Use EQEmu Oracle to explain quest::say`
- `Use EQEmu Oracle to find the schema for npc_types`
- `Use EQEmu Oracle to find the docs page for Perl quest events`
- `Use EQEmu Oracle to compare spell data tables related to NPC casting`

You do not need to manually start the plugin in normal use. Codex handles that through the included MCP configuration.

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

Ask Codex to find official docs and summarize them.

Examples:

- `Use EQEmu Oracle to find the documentation for bots`
- `Use EQEmu Oracle to summarize the task system docs`
- `Use EQEmu Oracle to find the EQEmu docs page about server configuration`

## How It Works

At a high level, the plugin gives Codex a local EQEmu reference source made from:

- quest scripting API data
- EQEmu schema data
- official EQEmu documentation snapshots

That means answers are based on the staged data in this repository instead of loose web browsing.

## Troubleshooting

### The Plugin Does Not Show Up In Codex

Make sure you opened the repository root first.

Then open the Plugins UI in Codex:

1. Check whether `EQEmu Oracle` already appears.
2. If it does not, use the Plugins UI option to load or import a local marketplace file.
3. Select `.agents/plugins/marketplace.json` from this repository root.
4. Install or enable `EQEmu Oracle` from that marketplace entry.

### Python Is Not Found

Install Python 3, then reopen the terminal and re-run the version check.

### Codex Sees The Plugin But It Does Not Answer

Run this manual check from the repository root:

Windows:

```powershell
py -3 plugins/eqemu-oracle/scripts/eqemu_oracle.py mcp-serve
```

macOS:

```sh
python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py mcp-serve
```

If the command starts cleanly, the runtime is available and Codex should be able to use it.

### You Downloaded Only The Plugin Folder

Download or clone the full repository instead. The plugin depends on files outside the plugin folder, including the local marketplace entry.

## Optional: Add Your Own Server Knowledge

If your server has custom tables, docs, or quest API behavior, you can add that on top of the built-in EQEmu data.

Use:

- `plugins/eqemu-oracle/extensions/` for shared repo-tracked additions
- `plugins/eqemu-oracle/local-extensions/` for machine-local additions you do not want in git

This is optional. Most users can ignore it until they need custom behavior.

## Optional: Point To Different Sources

If you want to use a fork, mirror, or different upstream source set:

1. Copy `plugins/eqemu-oracle/config/sources.toml`
2. Save it as `plugins/eqemu-oracle/config/sources.local.toml`
3. Change only the values you need

The local file is meant for personal overrides.

## Project Layout

If you want to know where things live:

- `.agents/plugins/marketplace.json`: local marketplace entry for Codex
- `plugins/eqemu-oracle/`: the actual plugin
- `plugins/eqemu-oracle/.codex-plugin/plugin.json`: plugin metadata
- `plugins/eqemu-oracle/.mcp.json`: local MCP server wiring
- `plugins/eqemu-oracle/scripts/`: runtime and CLI
- `plugins/eqemu-oracle/data/`: staged EQEmu data
- `plugins/eqemu-oracle/extensions/`: shared extension files
- `plugins/eqemu-oracle/local-extensions/`: local-only extension files

## In One Sentence

If you use Codex for EQEmu work, `EQEmu Oracle` gives it a clean, local source for scripting help, schema lookup, and documentation answers.
