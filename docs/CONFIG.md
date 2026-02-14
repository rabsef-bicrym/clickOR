# clickOR Config (JSON) Reference

This file describes the JSON schema that `clickor solve` expects.

clickOR configs are intentionally:

- **path-first**: the `path` strings must match `MediaFile.Path` exactly as ErsatzTV sees them
- **explicit**: no hidden path rewriting, no guessing
- **integer-ish**: the solver uses seconds internally; config uses `duration_min` floats only for ergonomics

Security note:

- clickOR treats config/YAML as **trusted input** (your own files).
- `clickor apply` constructs SQL and sends it to `sqlite3`.
- Do not use clickOR as a service that accepts untrusted configs from the internet without additional hardening.

## Top Level Shape

```json
{
  "channel": { "...": "..." },
  "schedule": { "...": "..." },
  "solver": { "...": "..." },
  "bumpers": { "...": "..." },
  "pools": { "...": "..." }
}
```

## `channel`

Required:

- `name` (string)
- `number` (int)

Optional:

- `group` (string)

Example:

```json
"channel": { "name": "Television", "number": 2, "group": "Television" }
```

## `schedule`

Optional (defaults will be filled if missing):

- `name` (string)
- `shuffle` (bool)
- `guide_mode` (string; `"include_all"` is typical)

Example:

```json
"schedule": { "name": "Television Schedule", "shuffle": false, "guide_mode": "include_all" }
```

Guide override note:

- In lineup YAML consumed by `clickor apply`, each `playlist.items[]` row may include `include_in_guide` (bool).
- When present, it overrides schedule-level guide defaults for that row.
- `clickor flat` uses this automatically so loop-expanded repeats can be guide-hidden after the first row.

## `solver`

This controls how the NP-hard block packing behaves.

Keys:

- `block_minutes` (number, default `30.0`)
  - This is the target block size for **content only** (bumpers are not counted).
- `longform_consumes_block` (bool, default `true`)
  - If `true`: any content item with duration `>= block_minutes` becomes a **solo block**
  - If `false`: everything is treated as short content to be packed (not recommended for movies)
- `allow_short_overflow_minutes` (number, default `0.0`)
  - Allows short blocks to exceed `block_minutes` by this much.
  - Example: `block_minutes=30` and `allow_short_overflow_minutes=2` means short blocks can go up to 32 minutes of content.
- `time_limit_sec` (int, default `60`)
  - Applied per CP-SAT phase (clickOR runs two phases).
- `seed` (int|string, default `0`)
  - If `0`, treated as "auto" (random seed chosen at runtime unless you pass `--seed`)
  - If int, used directly
  - If string, hashed deterministically to an int (so `"my seed"` is stable)

Example:

```json
"solver": {
  "block_minutes": 30.0,
  "longform_consumes_block": true,
  "allow_short_overflow_minutes": 0.0,
  "time_limit_sec": 60,
  "seed": 0
}
```

## `bumpers`

This controls the bumper insertion between blocks.

The playlist will always be:

```
bumper(s)  (exactly N items)
content-block (>= 1 item)
bumper(s)
content-block
...
```

Keys:

- `slots_per_break` (int, default `1`)
  - How many bumper items should appear between blocks.
- `mixing_strategy` (string)
  - `"round_robin"`: cycle through bumper pools by name order
  - `"weighted"`: choose a pool per slot based on weights
- `pools` (object mapping pool-name -> pool config)

Pool config keys:

- `weight` (number, default `1.0`)
- `items` (list of bumper items)

Bumper item keys:

- `path` (string; must match ErsatzTV MediaFile.Path)
- `duration_min` (number)
- `type` (string; one of `episode|movie|music_video|other_video`)

Example:

```json
"bumpers": {
  "slots_per_break": 2,
  "mixing_strategy": "weighted",
  "pools": {
    "coronet": {
      "weight": 3.0,
      "items": [
        { "path": "/media/other_videos/coronet/A.mkv", "duration_min": 10.5, "type": "other_video" }
      ]
    },
    "station_ids": {
      "weight": 1.0,
      "items": [
        { "path": "/media/other_videos/ids/ID1.mkv", "duration_min": 0.1, "type": "other_video" }
      ]
    }
  }
}
```

Exhaust-before-repeat:

- Each bumper pool is selected via an exhaust-before-repeat shuffle internally.
- clickOR also tries to avoid repeating the same bumper at the boundary between shuffles.

## `pools` (Content Pools)

Content pools define what "desirable content" is available and how it behaves.

Shape:

```json
"pools": {
  "pool_name": {
    "default_type": "other_video",
    "sequential": false,
    "repeat": { "...": "..." },
    "diversity": { "...": "..." },
    "items": [ { "...": "..." } ]
  }
}
```

Pool keys:

- `default_type` (required string)
- `sequential` (bool, default `false`)
  - If true, items must have `SxxExx` in the path; clickOR will enforce nondecreasing SxxExx over the whole playlist
- `repeat` (optional object)
  - `default_repeatable` (bool, default `false`)
  - `default_repeat_cost_min` (number, default `30`)
  - `default_max_extra_uses` (int, default `999`)
- `diversity` (optional object)
  - `dominant_block_threshold_min` (number, default `24`)
  - `dominant_block_penalty_min` (number, default `0`)

Item keys:

- `path` (required string)
- `duration_min` (required number)
- `type` (optional string; defaults to pool default_type)
- `repeatable` (optional bool; defaults to pool default_repeatable)
- `repeat_cost_min` (optional number; defaults to pool default_repeat_cost_min)
- `max_extra_uses` (optional int; defaults to pool default_max_extra_uses)

Repeat meaning:

- Base items appear exactly once in the minimal cycle.
- If a short block has spare capacity, clickOR may insert **repeat uses** of items marked repeatable.
- `repeat_cost_min` is a soft cost: higher values discourage repeats.
- `max_extra_uses` caps filler repeats for that item within one generated cycle.

Diversity meaning:

- clickOR tracks when a pool is "dominant" in a block (by time).
- It penalizes adjacent blocks that are both dominant for the same pool.
- This prevents "12 hours of Schoolhouse Rock blocks then move on" without forcing hard alternation constraints.

## Example Configs

See:

- `examples/example-config.json` (small, readable)
- `examples/television.json` (real-world sized)
