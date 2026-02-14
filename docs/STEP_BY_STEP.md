# Step-by-Step: Build a Channel With clickOR (Very Explicit)

This document is intentionally not terse.

The goal is: you can hand these instructions to another model (or a tired future-you) and it can "just do it" without inventing missing steps.

If you need exact item-by-item programming order (for example, Nana-style channels with title cards + feature + short film), use `docs/FLAT_PLAYLIST_RUNBOOK.md` instead of the solver flow in this file.

## Part 0: What You Are Building

You are building **one looping playlist** that ErsatzTV plays forever using a Flood schedule item.

clickOR enforces a programming pattern:

```
bumper(s)
content-block
bumper(s)
content-block
...
```

Terminology:

- A **bumper** is anything you want between blocks (Coronet films, IDs, promos, etc).
- A **content-block** is a group of desirable content items whose total duration is "about" your configured block size.
- Block size is in **content minutes only**. Bumpers are not counted toward the block size.

clickOR produces a **YAML lineup file** (it does not touch ErsatzTV).

Then clickOR can apply the YAML to the ErsatzTV sqlite DB (strictly by matching `MediaFile.Path`):

- `clickor solve` produces YAML
- `clickor verify` checks YAML against the config
- `clickor apply` creates/updates ErsatzTV objects in sqlite

## Part 1: One-Time Setup

In a terminal:

```bash
cd /path/to/clickor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
clickor --help
```

How you know it worked:

```bash
python -c "from ortools.sat.python import cp_model; print('ortools ok')"
```

## Part 2: Configure How clickOR Talks To ErsatzTV (Optional, But Recommended)

You only need this for:

- `clickor export-from-db` (reads the ErsatzTV sqlite DB)
- `clickor apply` (writes the ErsatzTV sqlite DB)

Create a `.env` file:

```bash
cd /path/to/clickor
cp .env.example .env
```

Now edit `.env`.

### Option A: Local DB Mode (You Are On The Same Machine As The DB)

Set:

- `CLICKOR_DB_PATH` to a real local path
- Leave `CLICKOR_SSH` empty

Example:

```
CLICKOR_DB_PATH=/Users/you/ersatztv.sqlite3
CLICKOR_SSH=
```

### Option B: Remote DB Mode (DB Lives On A NUC/Server)

Set:

- `CLICKOR_SSH` to an ssh prefix that can run commands on the remote host
- `CLICKOR_DB_PATH` to the DB path on the remote host
- `CLICKOR_SSH_SUDO=1` if the remote DB requires sudo to read/write

Example:

```
CLICKOR_SSH=ssh -i /Users/you/.ssh/your_key user@<host>
CLICKOR_DB_PATH=/mnt/media/config/ersatztv.sqlite3
CLICKOR_SSH_SUDO=1
```

Optional: allow `clickor apply` to reset channel playout after updating sqlite:

```
CLICKOR_BASE_URL=http://<host>:8409
CLICKOR_RESET_AFTER_APPLY=1
```

## Part 3: Create a Channel Config JSON

You need a channel config JSON before you can solve.

You have two options:

1. Export it from ErsatzTV DB (recommended, avoids copy/paste)
2. Write it manually (often using `clickor probe-dir` to get durations)

### Option A (Recommended): Export From DB

You maintain a small "export spec" that describes:

- which path prefixes should become bumpers
- which path prefixes should become content pools
- repeat/diversity knobs for those pools

Example spec:

- `examples/export-spec-television.json`

Export a concrete solve config:

```bash
clickor export-from-db \
  --spec examples/export-spec-television.json \
  --out /tmp/television.config.json
```

Now `/tmp/television.config.json` is a full solve config, ready for `clickor solve`.

### Option B: Manual Config (Fallback)

Start by copying:

```bash
cp examples/example-config.json /tmp/my-channel.config.json
```

Edit `/tmp/my-channel.config.json`.

When authoring manually, the hardest part is durations. If you do not have them, use `probe-dir`:

```bash
clickor probe-dir \
  --dir "/mnt/media/other_videos/coronet" \
  --rewrite-prefix "/mnt/media=/media" \
  --type other_video \
  --out /tmp/coronet.items.json
```

Then copy the `items` list from `/tmp/coronet.items.json` into your config under:

- `bumpers.pools.<name>.items` for bumpers
- `pools.<name>.items` for content

If you do not understand the schema, read:

- `docs/CONFIG.md`

## Part 4: Solve (Config JSON -> Lineup YAML)

Run:

```bash
clickor solve \
  --config /tmp/television.config.json \
  --out /tmp/television.yaml \
  --seed auto \
  --time-limit-sec 60 \
  --report /tmp/television.report.json
```

Important notes:

- The solver runs in **two CP-SAT phases**.
- `--time-limit-sec` applies **per phase**.
  - So `60` means "up to ~120 seconds total" (plus some overhead).

Seed behavior:

- If you pass `--seed <value>`, output becomes deterministic.
- If you pass `--seed auto`, you get a fresh randomized solve each run.
- If your config’s `solver.seed` is `0`, clickOR treats it as "auto" and will pick a random seed.

## Part 5: Verify (Recommended Safety Step)

Verify checks that:

- the bumper/content alternation is correct
- all content appears at least once
- "non-repeatable" items are not repeated
- bumpers exhaust-before-repeat per bumper pool
- blocks do not exceed capacity (except long-form solo items)
- sequential TV pools are in SxxExx order

Run:

```bash
clickor verify --config /tmp/television.config.json --yaml /tmp/television.yaml
```

`clickor solve` runs verify automatically unless you pass `--no-verify`.

## Part 6: Apply to ErsatzTV (YAML -> sqlite)

Always dry-run first:

```bash
clickor apply --yaml /tmp/television.yaml --dry-run --output /tmp/television.sql
```

If the SQL looks correct, apply:

```bash
clickor apply --yaml /tmp/television.yaml --apply --mode replace
```

Update modes:

- `--mode replace`:
  - For an existing channel/playlist, delete all existing `PlaylistItem`s and replace them with the new YAML items.
  - This is the safest "make it match the YAML exactly" mode.
- `--mode append`:
  - For an existing channel/playlist, keep existing `PlaylistItem`s and append the new YAML items to the end.
  - This is useful if you want to "add programming" without deleting what’s already there.

Playout reset:

- If `CLICKOR_BASE_URL` is set and `CLICKOR_RESET_AFTER_APPLY=1`, clickOR will POST a reset request after apply.
- You can override on a single run with `--reset 0` or `--reset 1`.

## If Something Goes Wrong

Read:

- `docs/TROUBLESHOOTING.md`
