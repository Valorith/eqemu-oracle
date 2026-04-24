# Hooks

EQEmu Oracle ships quiet Codex hooks that only act when they have strong evidence
that the user explicitly invoked the plugin or that Codex is maintaining plugin
data.

- `Stop`: asks Codex to continue when an explicit EQEmu Oracle answer appears
  ungrounded, lacks source/provenance context, or edits extension overlays
  without validation.
- `PostToolUse`: reviews Bash output from EQEmu Oracle maintenance commands such
  as refresh, rebuild, prune, update, and release bundle creation.

The hooks fail open by default. If they cannot parse the payload or cannot prove
that the current turn is relevant to EQEmu Oracle, they exit successfully without
output.
