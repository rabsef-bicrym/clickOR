# clickOR

Build deterministic, path-first ErsatzTV channels without fighting fragile manual playlist edits.

`clickOR` separates channel programming into two safe phases:

1. `solve`: turn channel intent (JSON) into a concrete looping lineup (YAML)
2. `apply`: update ErsatzTV sqlite state deterministically from that lineup

The result is repeatable channel operations, safer dry-runs, and easier iteration on programming ideas.

## Why clickOR

- Deterministic: same config + seed means predictable outputs
- Operationally safe: `solve` never touches the DB
- Debuggable: `apply` can emit SQL before execution
- Path-first: media identity is based on concrete library paths

## What You Can Program

- Long-form content channels (shows, films, music videos)
- Bumper/interstitial-driven channels
- Mixed channels with short-form + long-form blocks
- Flat playlists where very short clips can optionally auto-loop

## Quick Start

```bash
cd /path/to/clickor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
clickor --help
```

Optional environment file:

```bash
cp .env.example .env
```

Typical flow:

```bash
# 1) Export a concrete config from ErsatzTV
clickor export-from-db --spec examples/export-spec-television.json --out /tmp/television.config.json

# 2) Solve config -> lineup YAML
clickor solve --config /tmp/television.config.json --out /tmp/television.yaml --seed auto --report /tmp/television.report.json

# 3) Preview apply SQL (dry run)
clickor apply --yaml /tmp/television.yaml --dry-run --output /tmp/television.sql

# 4) Apply to ErsatzTV
clickor apply --yaml /tmp/television.yaml --apply --mode replace
```

## Core Docs

- `docs/STEP_BY_STEP.md`
- `docs/CONFIG.md`
- `docs/EXPORT_FROM_DB.md`
- `docs/PROBING.md`
- `docs/TROUBLESHOOTING.md`

## Notes On Naming

`clickOR` is the product name. `clickor` is used for the Python package and CLI command.
