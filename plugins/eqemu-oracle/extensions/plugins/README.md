# Perl Plugin Example Source Extensions

Create JSON files with a `sources` array.

Plugin source records point EQEmu Oracle at repositories or paths that are useful examples for reusable Perl quest plugin helpers. These records are indexed as context sources; they do not copy repository contents into the plugin dataset.

Use `context_key` to describe the role of a source. A local extension with the same `context_key` takes precedence over the repo-level source for that role, even when it points at a different fork or repository. Use a different `context_key` when you want to add a supplemental source instead of replacing the default.

`_example.json` in this folder is ignored by the loader and can be copied as a starting point.

```json
{
  "sources": [
    {
      "id": "my-perl-plugin-examples",
      "title": "My Perl Plugin Examples",
      "url": "https://github.com/example/custom-quests/tree/master/plugins",
      "source_type": "github_path",
      "context_key": "primary-perl-plugin-examples",
      "languages": ["perl"],
      "tags": ["perl plugins", "plugin:: helpers", "examples"],
      "description": "Server-specific Perl plugin helpers to use as examples.",
      "mode": "augment"
    }
  ]
}
```
