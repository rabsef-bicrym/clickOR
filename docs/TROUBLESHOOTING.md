# Troubleshooting

This is the “something went wrong, what do I do next?” guide.

## Problem: `ModuleNotFoundError: No module named 'ortools'`

Cause:

- You are not running inside a venv that has clickOR dependencies installed.

Fix:

```bash
cd /path/to/clickor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Problem: `clickor: command not found`

Cause:

- You did not install the package into your active environment.

Fix:

```bash
cd /path/to/clickor
source .venv/bin/activate
python -m pip install -e .
which clickor
clickor --help
```

## Problem: Solver takes “way longer than 60 seconds”

Cause:

- clickOR runs **two CP-SAT phases**.
- `solver.time_limit_sec` (or `--time-limit-sec`) applies **per phase**.
  - So `60` can mean ~120 seconds total (plus overhead).
- Large configs with many pools/items can legitimately take minutes.
- The solver model size grows roughly with `I_short × B` boolean variables.

Fix options:

1. Increase time limit:

```bash
clickor solve --config my.json --out /tmp/out.yaml --time-limit-sec 180
```

2. Reduce difficulty:

- Reduce diversity penalties
- Reduce repeatability (fewer filler options)
- Reduce pool count (especially many small pools)

Rule of thumb:

- clickOR is designed for "dozens to a few hundred items" per channel.
- If you have 1000+ items and want a single channel, consider splitting into multiple channels or exporting a filtered config.

## Problem: `CP-SAT could not find a feasible schedule (filler/diversity)`

Cause:

- Phase 1 (base packing) was feasible, but Phase 2’s repeat/diversity objective could not find a feasible model within the time limit.

Fix options:

1. Increase time limit (first thing to try).
2. Reduce diversity penalties (`dominant_block_penalty_min`).
3. Reduce repeatability or `max_extra_uses` in some pools.
4. If you set `allow_short_overflow_minutes` very small, consider increasing it slightly.

## Problem: `clickor apply` fails with `unable to open database file`

Cause:

- Your DB path is wrong for where the command is running.
- Or the DB is remote, but you didn’t provide SSH.

Fix checklist:

1. Decide where the DB is:

- Local file on this machine: use `--db` (or `CLICKOR_DB_PATH`)
- Remote file on a NUC/server: set `CLICKOR_SSH` and `CLICKOR_DB_PATH`

2. Dry-run apply:

```bash
clickor apply --yaml /tmp/channel.yaml --dry-run
```

3. If remote, confirm SSH works:

```bash
ssh -i /full/path/to/key user@host "sqlite3 /mnt/media/config/ersatztv.sqlite3 'select 1;'"
```

If that fails, fix SSH first.

## Problem: Apply fails with “no match for … at /media/…”

Cause:

- The YAML contains paths that do not exist in ErsatzTV’s sqlite DB.

Fix checklist:

1. Verify the file exists on disk in a directory ErsatzTV scans.
2. Verify ErsatzTV scanned it (UI).
3. Verify the exact stored path:

- Use `clickor export-from-db` to export a config and compare paths.
- If you used `probe-dir`, ensure `--rewrite-prefix` matches how ErsatzTV stores paths.

## Problem: Playout reset fails

Cause:

- `CLICKOR_BASE_URL` (or `--base-url`) is missing or wrong.
- Network/host is unavailable.

Fix:

- Set `CLICKOR_BASE_URL=http://<host>:8409`
- Or disable reset for that run: `clickor apply ... --reset 0`
