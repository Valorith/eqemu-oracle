# Quest Script Loading And File Resolution

## Why This Matters

When reviewing, debugging, or generating EQEmu quest scripts, determine which file actually loads before proposing changes. Do not assume a script is active just because a matching file exists.

## Perl Plugins

On this server, `/plugins` is a Perl-only global helper area. Treat files there as reusable Perl plugin code that can be called from other Perl quest scripts through `plugin::...` helpers. Do not treat `/plugins` as a Lua feature.

Upstream EQEmu docs also note that the `plugins` folder may live under `quests/` or be linked there, but the server convention documented here is a top-level `/plugins` content folder.

If a Perl quest script calls `plugin::SomeFunction(...)`, treat that as a direct signal that `SomeFunction` should be defined in a global Perl plugin file under `/plugins`, not in the local NPC, player, item, or other zone script itself.

## NPC Script Resolution

For NPC quest resolution, work through the hierarchy in order and stop at the first matching script.

1. `quests/<zone>/v<instance_version>/<npc_id>.[ext]`
2. `quests/<zone>/v<instance_version>/<npc_name>.[ext]`
3. `quests/<zone>/<npc_id>.[ext]`
4. `quests/<zone>/<npc_name>.[ext]`
5. `quests/global/<npc_id>.[ext]`
6. `quests/global/<npc_name>.[ext]`
7. `quests/<zone>/v<instance_version>/default.[ext]`
8. `quests/<zone>/default.[ext]`
9. `quests/global/default.[ext]`

Additional rules:

- An NPC script can be named either after the NPC's name or its `npc_types.id` / `NPCTypeID`.
- When both an ID-based file and a name-based file match in the same scope, the ID-based file wins.
- Lua only beats Perl when the basename is the same exact match, because upstream loading rules give `.lua` precedence over `.pl` for the same file name.
- A `quests/global/<npc_id>.[ext]` or `quests/global/<npc_name>.[ext]` script is global in scope and will be selected before zone or global `default.[ext]` fallbacks.
- `global_npc.[ext]` is different from `1234.pl`, `Some_NPC.lua`, or `default.pl`: `global_npc.[ext]` overlays on top of the selected NPC script instead of replacing the selected script.

## Global Overlay Scripts

The `quests/global` folder also supports overlay scripts that run in addition to the selected zone- or global-scoped script.

- `quests/global/global_player.[ext]`
- `quests/global/global_npc.[ext]`
- `quests/global/global_bot.[ext]`
- `quests/global/global_merc.[ext]`

Important behavior:

- `global_player.[ext]` and `global_npc.[ext]` are overlays, not replacements.
- If a zone-scoped `player.[ext]` or NPC script exists, the matching global overlay still executes on top of it.
- Upstream docs note that `global_npc.[ext]` runs in a zone-wide context, so NPCs in the same zone can see and change shared variables in that script context.

## Other Official Quest Loading Hierarchies

The official `Quest Loading` page also defines first-match load order for other quest types. Use these when the task is about non-NPC scripts.

### Players

1. `quests/<zone>/v<instance_version>/player.[ext]`
2. `quests/<zone>/player_v<instance_version>.[ext]`
3. `quests/<zone>/player.[ext]`
4. `quests/global/player.[ext]`

`global_player.[ext]` still overlays on top of the selected player script.

### Encounters

1. `quests/<zone>/v<instance_version>/encounters/<name>.[ext]`
2. `quests/<zone>/encounters/<name>.[ext]`
3. `quests/global/encounters/<name>.[ext]`

### Items

1. `quests/<zone>/v<instance_version>/items/<item_script>.[ext]`
2. `quests/<zone>/items/<item_script>.[ext]`
3. `quests/global/items/<item_script>.[ext]`
4. `quests/<zone>/items/default.[ext]`
5. `quests/global/items/default.[ext]`

### Spells

1. `quests/<zone>/v<instance_version>/spells/<spell_id>.[ext]`
2. `quests/<zone>/spells/<spell_id>.[ext]`
3. `quests/global/spells/<spell_id>.[ext]`
4. `quests/<zone>/spells/default.[ext]`
5. `quests/global/spells/default.[ext]`

### Bots

1. `quests/<zone>/v<instance_version>/bot.[ext]`
2. `quests/<zone>/bot_v<instance_version>.[ext]`
3. `quests/<zone>/bot.[ext]`
4. `quests/global/bot.[ext]`

### Mercenaries

1. `quests/<zone>/v<instance_version>/merc.[ext]`
2. `quests/<zone>/merc_v<instance_version>.[ext]`
3. `quests/<zone>/merc.[ext]`
4. `quests/global/merc.[ext]`

## Example Repository

For real-world examples, the ProjectEQ quest repository is a useful reference set:

- Repository: `https://github.com/ProjectEQ/projecteqquests`
- It contains zone folders, a `global` folder, and plugin examples that are helpful when the agent needs example script structure.

Use ProjectEQ as an example source, not as a substitute for the server's active file layout or the official EQEmu load order.

## Agent Guidance

Before editing a quest, answer these questions:

1. What script type is this: NPC, player, item, spell, encounter, bot, or mercenary?
2. Is there a version-specific `v<instance_version>` path that would load before the plain zone path?
3. Is there an ID-based file that beats a name-based file?
4. Is there a same-name `.lua` file that beats a `.pl` file?
5. Is a `quests/global/...` match replacing the zone fallback?
6. Is a `global_player.[ext]` or `global_npc.[ext]` overlay also running on top of the selected script?
7. If the task mentions `plugin::`, which global Perl plugin file under `/plugins` owns that helper?
8. If the task mentions Perl plugins, should reusable logic live in `/plugins` instead of being duplicated inside quest scripts?
