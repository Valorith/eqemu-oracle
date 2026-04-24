# Local Extensions

Place machine-local overlay files here using the same format as `extensions/`.

Files you create in this directory are private to your machine and ignored by git. The installer also creates editable `local.json` scaffold files for quest and plugin example sources when they are missing. Those files are used by the plugin after you add entries to their `sources` arrays, but they remain local-only and are preserved across installs.

The checked-in `_example.json` files are templates only; copy one to a new filename such as `my-server.json` when you want another private local file, then rebuild extensions.

Use the ignored starter templates in:

- `../extensions/quest-api/_example.json`
- `../extensions/schema/_example.json`
- `../extensions/docs/_example.json`
- `../extensions/quests/_example.json`
- `../extensions/plugins/_example.json`

Copy one into the matching local domain folder, rename it to a normal `.json` filename, then rebuild extensions.

This directory is intentionally ignored by git except for this README.
