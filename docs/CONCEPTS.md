# Concepts

This is the mental model clickOR is designed around.

## Playlist Shape

clickOR always generates a Flood playlist with alternating runs:

```
bumper(s)      (exactly N items)
content-block  (>= 1 item)
bumper(s)
content-block
...
```

Because Flood loops, the playlist must end with content (not bumpers), otherwise you get a bumper run boundary issue at the loop boundary.

## Blocks

Block time is **content only**.

- Target block size: `solver.block_minutes`
- Optional short overflow: `solver.allow_short_overflow_minutes`
- Longform rule: if `solver.longform_consumes_block=true`, any item whose duration is `>= block_minutes` becomes a solo block

The solver is trying to:

- pack content into blocks with minimal waste
- keep sequential TV pools ordered (SxxExx nondecreasing)
- avoid long streaks of the same pool dominating adjacent blocks
- use controlled repeats as filler (only for items marked repeatable)

## Repeats

clickOR generates a *minimal cycle* (every base item exactly once), then optionally inserts repeats as filler.

This matches the intent:

- "schedule all media in a group with minimal doubling up"
- "allow controlled doubling up where forced (forced by needing extra content to make a block)"

## Bumpers

Bumpers are not solved by CP-SAT.

They are selected in a deterministic post-processing step:

- each bumper pool is an exhaust-before-repeat shuffle
- pool selection per bumper slot is controlled by `bumpers.mixing_strategy`

