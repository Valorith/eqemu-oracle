# Expansion and Content Filtering

The purpose of this guide is to highlight the design of the content filtering system

## Goal(s)

To be able to filter content based on current server expansion setting and additionally by using more granular flags for more specific use cases such as patches, or temporary events such as Fabled

## Implementation

First, to accomplish filtering by Expansion we need to be able to set a filterable context on any content-facing table

## Content Tables

The following tables are content-facing tables that have had context filters applied to them

* doors
* fishing
* forage
* global_loot
* ground_spawns
* lootdrop
* loottable
* merchantlist
* object
* spawn2
* spawnentry
* start_zones
* starting_items
* tradeskill_recipe
* traps
* zone
* zone_points

## Content Filter Columns

```text
+------------------------+-----------------------+------+-----+----------+----------------+
| Field                  | Type                  | Null | Key | Default  | Extra          |
+------------------------+-----------------------+------+-----+----------+----------------+
| min_expansion          | tinyint(4) unsigned   | NO   |     | 0        |                |
| max_expansion          | tinyint(4) unsigned   | NO   |     | 0        |                |
| content_flags          | varchar(100)          | YES  |     | NULL     |                |
| content_flags_disabled | varchar(100)          | YES  |     | NULL     |                |
+------------------------+-----------------------+------+-----+----------+----------------+
```

## How the Filtering Works

There are two main categories of filtering, expansion filtering and content flag filtering

### Expansion Filtering

Expansion filtering is done through **min_expansion** and **max_expansion** fields.

The data filtering works by first using the current expansion context setting of the server. As of this writing today the **Expansion ID** is loaded from Rule **Expansion:CurrentExpansion** which has a value that corresponds with [Expansion List Reference](expansion-list-reference.md)

### Content Flag Filtering

There are use cases where we need to enable or disable content table entries based on certain events, such as Fabled NPC spawns or a particular patch that was made in the past where perhaps a Merchant is no longer selling an item. Maybe the sleeper was killed and we no longer want an item to appear on a loot table, no longer want the sleeper to be able to be awakened etc.

```text
+-----------+-------------+------+-----+---------+----------------+
| Field     | Type        | Null | Key | Default | Extra          |
+-----------+-------------+------+-----+---------+----------------+
| id        | int(11)     | NO   | PRI | NULL    | auto_increment |
| flag_name | varchar(75) | YES  |     | NULL    |                |
| enabled   | tinyint(4)  | YES  |     | NULL    |                |
| notes     | text        | YES  |     | NULL    |                |
+-----------+-------------+------+-----+---------+----------------+
```

### How the Filters Apply to Queries

The Expansion ID is set on process boot up as well as the content flags. Both of these get applied anytime a query is executed that goes through the content tables that use the **ContentFilterCriteria**

```text
ContentFilterCriteria::apply(std::string table_prefix = "")
```

�When any query runs through this function it has a set of criteria appended to the query that will determine how data returns back from the table

### Example Query

**Data** Merchants

**Current Expansion** -1 (All enabled)

**Content Flags** test_flag

```sql
-- merchant query
...
AND (
  merchantlist.min_expansion <= 99
  OR merchantlist.min_expansion = 0
)
AND (
  merchantlist.max_expansion >= 99
  OR merchantlist.max_expansion = 0
)
AND (
  merchantlist.content_flags IS NULL
  OR CONCAT(',', merchantlist.content_flags, ',') REGEXP ',(test_flag),'
)
AND (
  merchantlist.content_flags_disabled IS NULL
  OR CONCAT(',', merchantlist.content_flags_disabled, ',') NOT REGEXP ',(test_flag),'
)
```

To explain the above filter a little bit, if our Merchant table didn't have any values set, it would load everything

If it had a flag set on a field, that field or row would ONLY load if the flag was enabled in the **content_flags** table.

If there is an expansion value different than the default 0, then the current expansion would filter against whether or not it should load

### Default Values

When **min_expansion** or **max_expansion** are -1; that means that they are loaded in all expansion contexts regardless of what is set at the server level

When **content_flags** or **content_flags_disabled** are **NULL;** it means that they can be loaded in all scenarios

### Multiple Content Flags

Multiple content flags can be set comma separated in the flags field of any content table

For example if I had a spawn table entry with **content_flags** `gm_event_2020,fabled` ****that means that if the server had **either** the GM event flag or the fabled flag, the spawn data would be loaded at the server level

The exact opposite works for **content_flags_disabled** field where if I had `gm_event_2020,fabled` as values in the disabled field, the spawn data will be disabled or not be loaded if either of the flags are enabled at the server level

### In Game Example

Take **Plane of Knowledge** books, they only started existing during **Planes of Power**; so we need to be able to shut them off on any expansion prior to **Planes of Power**

Our **Expansion:CurrentExpansion** value is set to **4 (Planes of Power)** as you can see in **Everfrost** we have a PoK book that we would typically expect

![](../../gitbook/assets/image%20(14).png)

```sql
SELECT id, zone, name, min_expansion, max_expansion FROM doors WHERE name like '%POKTELE%' and zone = 'everfrost'
+------+-----------+------------+---------------+---------------+
| id   | zone      | name       | min_expansion | max_expansion |
+------+-----------+------------+---------------+---------------+
| 3415 | Everfrost | POKTELE500 |             4 |             0 |
+------+-----------+------------+---------------+---------------+
```

This is what we would expect; our minimum expansion on this door is set to 4, so since our current expansion is at that number we load as expected.

So what happens if set our expansions back a notch or two?

```sql
MariaDB [peq]> update rule_values set rule_value = 2 WHERE rule_name = 'Expansion:CurrentExpansion';
Query OK, 1 row affected (0.005 sec)
Rows matched: 1  Changed: 1  Warnings: 0
```

We've set ourselves back to **The Scars of Velious** expansion and need to hot reload in game to see the results; I've issued a **#rules reload** to bring in the new rule value into memory and a **#reloadstatic** to set the expansion and content context from the newly set rule value

![](../../gitbook/assets/image%20(12).png)

You can see now that we have no **Plane of Knowledge** book with a seamless content transition without harsh removal of actual data to achieve the affect of bringing the server into the context of an era

## Expansions Quest API

There are instances where extending the current expansion context into easily readable quest calls would help solve for use cases to make seamless behavioral transitions in quest scripts

### Examples

1) We have an NPC that existed already in for example expansion 4; **Planes of Power** but a few expansion down the line, the dialogue of this NPC has changed. We need a boolean or conditional that we can check to see what current expansion context the server is in before making behavioral changes in our script

```perl
if (quest::is_current_expansion_dragons_of_norrath()) {
    # New Quest Dialogue
    if ($text=~/hail/i) {
        # Some text
    }
}
```

2) We a good handful of classic zones; later down the line in **Lost Dungeons of Norrath** we get these messages when entering the zones

> A mysterious voice whispers to you, 'If you can feel me in your thoughts, know this -- something is changing in the world and I reckon you should be a part of it. I do not know much, but I do know that in every home city and the wilds there are agents of an organization called the Wayfarers Brotherhood. They are looking for recruits . . . If you can hear this message, you are one of the chosen. Rush to your home city, or search the West Karanas and Rathe Mountains for a contact if you have been exiled from your home for your deeds, and find out more. Adventure awaits you, my friend.''

Before these new additions there was nothing to conditionally check which expansion was enabled or disabled to determine whether or not to even display this message, which could be solved with the addition of **the following call.** If we set our server to an earlier expansion these messages would disappear

```perl
eq.is_lost_dungeons_of_norrath_enabled()
```

What this looks like in the **global_player.lua**

=== "global_player.lua"

    ```lua
    function event_enter_zone(e)
        local qglobals = eq.get_qglobals(e.self);
        if(e.self:GetLevel() >= 15 and qglobals['Wayfarer'] == nil and eq.is_lost_dungeons_of_norrath_enabled()) then
            local zoneid = eq.get_zone_id();
            if(e.self:GetStartZone() ~= zoneid and (zoneid == 1 or zoneid == 2 or zoneid == 3 or zoneid == 8 or zoneid == 9 
            or zoneid == 10 or zoneid == 19 or zoneid == 22 or zoneid == 23 or zoneid == 24 or zoneid == 29 or zoneid == 30 
            or zoneid == 34 or zoneid == 35 or zoneid == 40 or zoneid == 41 or zoneid == 42 or zoneid == 45 or zoneid == 49 
            or zoneid == 52 or zoneid == 54 or zoneid == 55 or zoneid == 60 or zoneid == 61 or zoneid == 62 or zoneid == 67 
            or zoneid == 68 or zoneid == 75 or zoneid == 82 or zoneid == 106 or zoneid == 155 or zoneid == 202 or zoneid == 382 
            or zoneid == 383 or zoneid == 392 or zoneid == 393 or zoneid == 408)) then
                e.self:Message(15, 
                    "A mysterious voice whispers to you, 'If you can feel me in your thoughts, know this -- "
                    .. "something is changing in the world and I reckon you should be a part of it. I do not know much, but I do know "
                    .. "that in every home city and the wilds there are agents of an organization called the Wayfarers Brotherhood. They "
                    .. "are looking for recruits . . . If you can hear this message, you are one of the chosen. Rush to your home city, or "
                    .. "search the West Karanas and Rathe Mountains for a contact if you have been exiled from your home for your deeds, "
                    .. "and find out more. Adventure awaits you, my friend.'");
            end
        end
    end
    ```


### API

=== "Lua"

    ```lua
    eq.is_classic_enabled()
    eq.is_the_ruins_of_kunark_enabled()
    eq.is_the_scars_of_velious_enabled()
    eq.is_the_shadows_of_luclin_enabled()
    eq.is_the_planes_of_power_enabled()
    eq.is_the_legacy_of_ykesha_enabled()
    eq.is_lost_dungeons_of_norrath_enabled()
    eq.is_gates_of_discord_enabled()
    eq.is_omens_of_war_enabled()
    eq.is_dragons_of_norrath_enabled()
    eq.is_depths_of_darkhollow_enabled()
    eq.is_prophecy_of_ro_enabled()
    eq.is_the_serpents_spine_enabled()
    eq.is_the_buried_sea_enabled()
    eq.is_secrets_of_faydwer_enabled()
    eq.is_seeds_of_destruction_enabled()
    eq.is_underfoot_enabled()
    eq.is_house_of_thule_enabled()
    eq.is_veil_of_alaris_enabled()
    eq.is_rain_of_fear_enabled()
    eq.is_call_of_the_forsaken_enabled()
    eq.is_the_darkend_sea_enabled()
    eq.is_the_broken_mirror_enabled()
    eq.is_empires_of_kunark_enabled()
    eq.is_ring_of_scale_enabled()
    eq.is_the_burning_lands_enabled()
    eq.is_torment_of_velious_enabled()
    eq.is_current_expansion_classic()
    eq.is_current_expansion_the_ruins_of_kunark()
    eq.is_current_expansion_the_scars_of_velious()
    eq.is_current_expansion_the_shadows_of_luclin()
    eq.is_current_expansion_the_planes_of_power()
    eq.is_current_expansion_the_legacy_of_ykesha()
    eq.is_current_expansion_lost_dungeons_of_norrath()
    eq.is_current_expansion_gates_of_discord()
    eq.is_current_expansion_omens_of_war()
    eq.is_current_expansion_dragons_of_norrath()
    eq.is_current_expansion_depths_of_darkhollow()
    eq.is_current_expansion_prophecy_of_ro()
    eq.is_current_expansion_the_serpents_spine()
    eq.is_current_expansion_the_buried_sea()
    eq.is_current_expansion_secrets_of_faydwer()
    eq.is_current_expansion_seeds_of_destruction()
    eq.is_current_expansion_underfoot()
    eq.is_current_expansion_house_of_thule()
    eq.is_current_expansion_veil_of_alaris()
    eq.is_current_expansion_rain_of_fear()
    eq.is_current_expansion_call_of_the_forsaken()
    eq.is_current_expansion_the_darkend_sea()
    eq.is_current_expansion_the_broken_mirror()
    eq.is_current_expansion_empires_of_kunark()
    eq.is_current_expansion_ring_of_scale()
    eq.is_current_expansion_the_burning_lands()
    eq.is_current_expansion_torment_of_velious()
    ```

=== "Perl"

    ```perl
    quest::is_classic_enabled()
    quest::is_the_ruins_of_kunark_enabled()
    quest::is_the_scars_of_velious_enabled()
    quest::is_the_shadows_of_luclin_enabled()
    quest::is_the_planes_of_power_enabled()
    quest::is_the_legacy_of_ykesha_enabled()
    quest::is_lost_dungeons_of_norrath_enabled()
    quest::is_gates_of_discord_enabled()
    quest::is_omens_of_war_enabled()
    quest::is_dragons_of_norrath_enabled()
    quest::is_depths_of_darkhollow_enabled()
    quest::is_prophecy_of_ro_enabled()
    quest::is_the_serpents_spine_enabled()
    quest::is_the_buried_sea_enabled()
    quest::is_secrets_of_faydwer_enabled()
    quest::is_seeds_of_destruction_enabled()
    quest::is_underfoot_enabled()
    quest::is_house_of_thule_enabled()
    quest::is_veil_of_alaris_enabled()
    quest::is_rain_of_fear_enabled()
    quest::is_call_of_the_forsaken_enabled()
    quest::is_the_darkend_sea_enabled()
    quest::is_the_broken_mirror_enabled()
    quest::is_empires_of_kunark_enabled()
    quest::is_ring_of_scale_enabled()
    quest::is_the_burning_lands_enabled()
    quest::is_torment_of_velious_enabled()
    quest::is_current_expansion_classic()
    quest::is_current_expansion_the_ruins_of_kunark()
    quest::is_current_expansion_the_scars_of_velious()
    quest::is_current_expansion_the_shadows_of_luclin()
    quest::is_current_expansion_the_planes_of_power()
    quest::is_current_expansion_the_legacy_of_ykesha()
    quest::is_current_expansion_lost_dungeons_of_norrath()
    quest::is_current_expansion_gates_of_discord()
    quest::is_current_expansion_omens_of_war()
    quest::is_current_expansion_dragons_of_norrath()
    quest::is_current_expansion_depths_of_darkhollow()
    quest::is_current_expansion_prophecy_of_ro()
    quest::is_current_expansion_the_serpents_spine()
    quest::is_current_expansion_the_buried_sea()
    quest::is_current_expansion_secrets_of_faydwer()
    quest::is_current_expansion_seeds_of_destruction()
    quest::is_current_expansion_underfoot()
    quest::is_current_expansion_house_of_thule()
    quest::is_current_expansion_veil_of_alaris()
    quest::is_current_expansion_rain_of_fear()
    quest::is_current_expansion_call_of_the_forsaken()
    quest::is_current_expansion_the_darkend_sea()
    quest::is_current_expansion_the_broken_mirror()
    quest::is_current_expansion_empires_of_kunark()
    quest::is_current_expansion_ring_of_scale()
    quest::is_current_expansion_the_burning_lands()
    quest::is_current_expansion_torment_of_velious()
    ```


## Content Flag Quest API

There are cases where you may want to check the state of current content flags in game via quest to then potentially trigger other events. For that reason there is a simple **Quest API** that has been created to expose

### Example

=== "Lua"
    ```lua
    function event_say(e)
        local flag_name = 'some_flag';
        
        e.self:Message(15, "################################");
        e.self:Message(15, "# Testing Content Flags");
        e.self:Message(15, "################################");
    
        e.self:Message(15, "Setting flag [" .. flag_name .. "] to disabled");
        eq.set_content_flag(flag_name, false);
        e.self:Message(15, "Content flag is [" .. (eq.is_content_flag_enabled(flag_name) and "enabled" or "disabled") .. "]");
        e.self:Message(15, "Setting flag [" .. flag_name .. "] to enabled");
        eq.set_content_flag(flag_name, true);
        e.self:Message(15, "Content flag is [" .. (eq.is_content_flag_enabled(flag_name) and "enabled" or "disabled") .. "]");
    end
    ```
=== "Perl"
    ```perl
    sub EVENT_SAY {
        my $flag_name = "some_flag";
    
        $client->Message(15, "################################");
        $client->Message(15, "# Testing Content Flags");
        $client->Message(15, "################################");
    
        $client->Message(15, "Setting flag [" . $flag_name . "] to disabled");
        quest::set_content_flag($flag_name, 0);
        $client->Message(15, "Content flag is [" . (quest::is_content_flag_enabled($flag_name) ? "enabled" : "disabled") . "]");
        $client->Message(15, "Setting flag [" . $flag_name . "] to enabled");
        quest::set_content_flag($flag_name, 1);
        $client->Message(15, "Content flag is [" . (quest::is_content_flag_enabled($flag_name) ? "enabled" : "disabled") . "]");	
    }
    ```

### Result

```text
################################
# Testing Content Flags
################################
Setting flag [some_flag] to disabled
Content flag is [disabled]
Setting flag [some_flag] to enabled
Content flag is [enabled]
```

### Table Example

```lua
MariaDB [peq]> select * from content_flags where flag_name = 'some_flag';
+----+-----------+---------+-------+
| id | flag_name | enabled | notes |
+----+-----------+---------+-------+
|  9 | some_flag |       1 |       |
+----+-----------+---------+-------+
```

### API

=== "Lua"
    ```lua
    eq.is_content_flag_enabled(string flag_name)
    eq.set_content_flag(string flag_name, bool enabled)
    ```
=== "Perl"
    ```perl
    quest::is_content_flag_enabled(string flag_name)
    quest::set_content_flag(string flag_name, bool enabled)
    ```

