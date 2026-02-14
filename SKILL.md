---
name: clickor
description: Operate clickOR to create and program ErsatzTV channels safely from content sources to final playout. Use when an agent needs to gather media, build channel configs, run solve/verify/apply, handle remote SSH DB access, and avoid direct manual ErsatzTV DB programming mistakes.
---

# clickOR Operator Skill

Use this skill to operate clickOR for real channel programming work.  
Do not treat this as a repo-contribution guide.

## Required Read Order

Before running commands, read these files in order:

1. `README.md`
2. `docs/CONCEPTS.md`
3. `docs/STEP_BY_STEP.md`
4. `docs/FLAT_PLAYLIST_RUNBOOK.md` (for exact-order channels and looped shorts)
5. `docs/CONFIG.md`
6. `docs/TROUBLESHOOTING.md` (only if blocked)

## Mental Model

1. clickOR is path-first. File paths are identity.
2. clickOR solves a deterministic looping lineup YAML.
3. clickOR then applies that YAML into ErsatzTV sqlite.
4. Avoid direct handcrafted SQL unless explicitly requested.

## Standard Workflow (Always This Sequence)

1. Prepare media files and ensure final storage paths are stable.
2. Build channel config:
   - Preferred: `clickor export-from-db` using an export spec.
   - Fallback: manual JSON config with probed durations.
3. Solve:
   - `clickor solve --config ... --out ... --report ...`
4. Verify:
   - `clickor verify --config ... --yaml ...`
5. Apply dry-run first:
   - `clickor apply --yaml ... --dry-run --output ...`
6. Apply live only after dry-run checks:
   - `clickor apply --yaml ... --apply --mode replace`

Never skip `verify` or dry-run unless the user explicitly asks.

## Remote and Environment Defaults

Use `.env` with these keys when targeting remote ErsatzTV:

- `CLICKOR_SSH`
- `CLICKOR_DB_PATH`
- `CLICKOR_SSH_SUDO`
- `CLICKOR_BASE_URL`
- `CLICKOR_RESET_AFTER_APPLY`

If `.env` is absent, require explicit `--ssh` / `--db` flags for apply/export commands.

## Content Acquisition Guidance

When user asks for channel themes (example: vintage western channel):

1. Gather source media from requested providers (archive.org, YouTube, etc.).
2. Normalize container/codec only when needed for reliable playout.
3. Place assets in a predictable path taxonomy before config generation.
4. Separate content pools from bumper/interstitial pools intentionally.
5. Preserve provenance notes externally if user requests traceability.

Do not infer durations from filenames or metadata guesses; probe or export them.

## Flat Mode Rules

Use `clickor flat` only when user wants explicit ordered playback (no solver packing).

1. `loop_to` is explicit per-item repeat target.
2. `auto_loop` controls whether short-item auto-loop is allowed.
3. Set `auto_loop: false` for title cards/slates that should not repeat.
4. Auto-looped repeats are guide-collapsed by default:
   - first repeated row is guide-visible
   - additional repeats are guide-hidden
5. This behavior is path/type agnostic (`other_video` can still loop when intended).
6. For Nana-style channels, follow `docs/FLAT_PLAYLIST_RUNBOOK.md` exactly.

## Safety Guardrails

1. Keep `solve` and `apply` separate in reasoning and execution.
2. Prefer `--mode replace` unless user asks for append semantics.
3. Never do silent path rewriting or hidden normalization.
4. If unresolved media paths appear, stop and report exact missing paths.
5. If requirements imply full daypart scheduling semantics, call out that this is advanced and may require careful schedule-model integration.

## Minimal Command Set

```bash
clickor export-from-db --spec <spec.json> --out /tmp/channel.config.json
clickor probe-dir --dir <path> --type <type> --out /tmp/items.json
clickor solve --config /tmp/channel.config.json --out /tmp/channel.yaml --seed auto --report /tmp/channel.report.json
clickor verify --config /tmp/channel.config.json --yaml /tmp/channel.yaml
clickor apply --yaml /tmp/channel.yaml --dry-run --output /tmp/channel.sql
clickor apply --yaml /tmp/channel.yaml --apply --mode replace
clickor flat /tmp/nanas-flat.json --mode replace --output /tmp/nanas-flat.sql
clickor flat /tmp/nanas-flat.json --mode replace --apply
```
