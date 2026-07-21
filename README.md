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
- Flat playlists where looped short clips collapse duplicate guide rows
- Companion cards spliced next to specific items ("And now:" title cards,
  per-film slates, block-opening idents) — see Companions below

## Companions

Bumpers are anonymous — shuffled from pools between blocks. Companions are
addressed: a card that belongs to one item and travels with it. The solver
never sees them; they are spliced into the final order afterward. The solver
owns the programs; injection is a choice.

Add an optional top-level `companions` list to a solve or flat config:

```json
"companions": [
  {
    "match": {"pools": ["coronet"]},
    "scope": "every_match",
    "position": "before",
    "card": {"template": "/media/other_videos/Cards/Coronet/{stem}.mp4",
             "type": "other_video", "include_in_guide": true}
  },
  {
    "match": {"types": ["episode"], "path_glob": "*/MST3K/*"},
    "card": {"template": "/media/other_videos/Cards/MST3K/{stem}.mp4"}
  },
  {
    "match": {"pools": ["feature"]},
    "scope": "block_start",
    "card": {"template": "/media/other_videos/Cards/Idents/movies.mp4"}
  }
]
```

- `match` — all given conditions must hold: `pools` (solve mode only),
  `types`, `path_glob` (fnmatch).
- `scope` — `every_match` (default) or `block_start` (first matched item of
  each content block; solve mode only).
- `position` — `before` (default) or `after` the item.
- `card.template` — format string with `{stem}` (basename, no extension),
  `{name}`, `{dir}`. `card.map` gives exact item-path -> card-path overrides
  and wins over the template. A matched item with no resolvable card is an
  error, not a silent drop.
- Cards count as furniture in `verify`: excluded from block-capacity sums and
  the long-form one-item rule, and a dedicated adjacency check fails the
  lineup if any matched item is missing its card (or grew an extra one).

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
- `docs/FLAT_PLAYLIST_RUNBOOK.md`
- `docs/CONFIG.md`
- `docs/EXPORT_FROM_DB.md`
- `docs/PROBING.md`
- `docs/TROUBLESHOOTING.md`

## Notes On Naming

`clickOR` is the product name. `clickor` is used for the Python package and CLI command.
