# Export From ErsatzTV DB (No Copy/Paste)

`clickor export-from-db` exists to eliminate "copy a list of files into JSON by hand".

Instead, you maintain a small export spec that says:

- which path prefixes belong to which pools
- what knobs apply to those pools

Then clickOR queries ErsatzTV’s sqlite DB for:

- exact `MediaFile.Path` values (as ErsatzTV sees them)
- exact `MediaVersion.Duration` values (as stored by ErsatzTV)
- inferred media type (episode/movie/music_video/other_video)

Output is a full clickOR solve config JSON (ready for `clickor solve`).

## Requirements

- ErsatzTV must have scanned the media already (durations must be non-zero in sqlite).
- You must have access to the sqlite DB (local file path, or SSH access to the host).

## Usage

```bash
clickor export-from-db \
  --spec examples/export-spec-television.json \
  --out /tmp/television.config.json
```

Connection configuration is via `.env` or flags:

- `CLICKOR_DB_PATH` or `--db`
- `CLICKOR_SSH` or `--ssh`
- `CLICKOR_SSH_SUDO` or `--ssh-sudo`

## Export Spec Schema (Practical)

An export spec looks like:

```json
{
  "channel": { "name": "Television", "number": 2, "group": "Television" },
  "schedule": { "name": "Television Schedule", "shuffle": false, "guide_mode": "include_all" },
  "solver": { "block_minutes": 30.0, "time_limit_sec": 60, "seed": 0 },
  "bumpers": {
    "slots_per_break": 1,
    "mixing_strategy": "round_robin",
    "pools": {
      "coronet": {
        "weight": 1.0,
        "only_types": ["other_video"],
        "include_path_prefixes": ["/media/other_videos/coronet/"]
      }
    }
  },
  "pools": {
    "krtek": {
      "default_type": "episode",
      "sequential": true,
      "only_types": ["episode"],
      "include_path_prefixes": ["/media/shows/krtek/"]
    }
  }
}
```

Meaning:

- `include_path_prefixes` are SQL `LIKE '<prefix>%` matches against `MediaFile.Path`.
- `only_types` lets you filter by inferred media type from `MediaVersion`.
- `include_contains` / `exclude_contains` (optional) do substring filtering on the path (use sparingly).
- For content pools, you can provide `repeat`, `diversity`, and `overrides` blocks. Those values are copied into the output solve config.

## Important: Path Identity

The exported `path` values must match what ErsatzTV stores exactly.

If your files are on disk at `/mnt/media/...` but ErsatzTV sees them as `/media/...`, you must make that consistent in ErsatzTV, not in clickOR.

clickOR’s job is to avoid guessing.

