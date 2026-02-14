# Flat Playlist Runbook (Nana-Style Channels)

Use this when you want exact programming order, not solver block packing.

This is the easiest way to build channels like:

1. interstitial card introducing content
2. full movie
3. interstitial card introducing short
4. short film

and loop it as one Flood playlist in ErsatzTV.

## Why Flat Mode For This

`clickor flat` preserves your exact item order and handles short-loop behavior directly.

Guide behavior for looped shorts is built in:

- loop-generated repeats are still in playout
- only the first row is guide-visible
- repeated rows are guide-hidden

This prevents duplicate guide spam for very short looped items.

## Loop Rules You Need

Top-level defaults:

- `loop_short_under`: items shorter than this are eligible for auto-loop
- `loop_short_to`: loop eligible items up to this target duration

Per-item overrides:

- `auto_loop: false` -> do not auto-loop this item (use for title cards/slates)
- `loop_to: <seconds>` -> explicit loop target for this item

Paths are type-agnostic. You can keep shorts under `other_video` and still use this.

## Minimal Config Example

```json
{
  "mode": "flat",
  "channel_name": "Nana's Picks",
  "channel_number": 6,
  "playlist_name": "Nana's Picks Playlist",
  "playlist_group": "Nana's Picks",
  "schedule_name": "Nana's Picks Schedule",
  "schedule_shuffle": false,
  "schedule_guide_mode": "include_all",
  "loop_short_under": 15,
  "loop_short_to": 30,
  "items": [
    {
      "type": "other_video",
      "path": "/media/other_videos/Classics - Interstitials/feature_intro.mp4",
      "auto_loop": false
    },
    {
      "type": "movie",
      "path": "/media/movies/Duck Soup (1933)/Duck Soup (1933).mp4"
    },
    {
      "type": "other_video",
      "path": "/media/other_videos/Classics - Interstitials/short_intro.mp4",
      "auto_loop": false
    },
    {
      "type": "other_video",
      "path": "/media/other_videos/Classic Bumpers/Race Horse.mp4"
    }
  ]
}
```

## Execution Pipeline

1. Ensure `.env` points to the correct ErsatzTV host/DB.
2. Generate SQL without applying:

```bash
clickor flat /tmp/nanas-flat.json --mode replace --output /tmp/nanas-flat.sql
```

3. Apply when SQL looks correct:

```bash
clickor flat /tmp/nanas-flat.json --mode replace --apply
```

4. Reset playout (if your process requires immediate schedule refresh).

If you want clickOR-managed reset in one command path, use `clickor apply` workflow with YAML and `--reset`.

## If You Hand-Author YAML

When using `clickor apply --yaml ...` directly, you can control guide visibility per row:

```yaml
playlist:
  items:
    - path: /media/other_videos/clip.mp4
      type: other_video
      include_in_guide: true
    - path: /media/other_videos/clip.mp4
      type: other_video
      include_in_guide: false
```

Use this only when you intentionally manage expanded repeats yourself.
