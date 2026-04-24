# Quest Example Source Extensions

Create JSON files with a `sources` array.

Quest source records point EQEmu Oracle at repositories or paths that are useful examples for Perl and Lua quest script work. These records are indexed as context sources; they do not copy repository contents into the plugin dataset.

Use `context_key` to describe the role of a source. A local extension with the same `context_key` takes precedence over the repo-level source for that role, even when it points at a different fork or repository. Use a different `context_key` when you want to add a supplemental source instead of replacing the default.

`_example.json` in this folder is ignored by the loader and can be copied as a starting point.

```json
{
  "sources": [
    {
      "id": "my-quest-script-examples",
      "title": "My Quest Script Examples",
      "url": "https://github.com/example/custom-quests",
      "source_type": "github_repo",
      "context_key": "primary-quest-script-examples",
      "languages": ["perl", "lua"],
      "tags": ["quest scripts", "examples"],
      "description": "Server-specific quest scripts to use as examples when writing or reviewing quests.",
      "mode": "augment"
    }
  ]
}
```
