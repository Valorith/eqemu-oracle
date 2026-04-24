# Repo Extensions

Place repo-tracked overlay files here when you want custom data to be shared with everyone using this repository.

These files are merged on top of the committed upstream snapshot and become part of the shared effective dataset.

## Precedence

Merge order is:

1. base upstream data
2. repo extension
3. local extension

If a local extension reuses the same id, it wins over the repo extension on that machine only.

## Domains

- `quest-api/`: extra or overridden Perl/Lua scripting API records
- `schema/`: extra or overridden table definitions and relationships
- `docs/`: extra or overridden docs pages, aliases, and tags
- `quests/`: example quest script source repositories or paths
- `plugins/`: example Perl plugin source repositories or paths

## Record Modes

Each extension record can declare:

- `override`
- `augment`
- `disable`

If `mode` is omitted, existing ids default to `override` and new ids default to `augment`.

## Format

Each domain uses a different top-level array:

- quest API: `records`
- schema: `tables`
- docs: `pages`
- quests: `sources`
- plugins: `sources`

The reserved filename `_example.json` is ignored by the loader and can be used as a safe template in any extension folder.

Current starter templates:

- `extensions/quest-api/_example.json`
- `extensions/schema/_example.json`
- `extensions/docs/_example.json`
- `extensions/quests/_example.json`
- `extensions/plugins/_example.json`

See the domain-specific readmes in each folder for concrete examples.
