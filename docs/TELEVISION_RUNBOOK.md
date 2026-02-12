# Television Runbook (From Existing Config To Live ErsatzTV)

This is the exact “do the thing” path for the existing config:

`examples/television.json`

Goal:

- generate a YAML playlist lineup
- verify it is structurally correct
- apply it to ErsatzTV (update the sqlite DB)

This assumes:

- you are on the Mac in this repo
- your `.env` is configured to reach the ErsatzTV sqlite DB (local or remote)

If any of those are not true, stop and fix that first.

---

## 1) Enter The Repo

```bash
cd /path/to/clickor
```

---

## 2) Activate The Venv

If you do not have a venv yet, follow `clickor/docs/STEP_BY_STEP.md` Part 1.

If you already have it:

```bash
source .venv/bin/activate
```

---

## 3) Solve (Generate YAML)

```bash
clickor solve \
  --config examples/television.json \
  --out /tmp/television.yaml \
  --seed auto \
  --report /tmp/television.report.json
```

Expected output:

- it prints the number of blocks, repeats used, and total waste
- it writes `/tmp/television.yaml`

---

## 4) Verify (Before Touching ErsatzTV)

```bash
clickor verify \
  --config examples/television.json \
  --yaml /tmp/television.yaml
```

Expected output:

- `OK: 0 warning(s)`

If it is not OK, stop here.

---

## 5) Dry-Run The DB Update (Remote DB Required)

Dry-run still needs DB access because it resolves `path -> MediaItemId`.

```bash
clickor apply --yaml /tmp/television.yaml \
  --dry-run \
  --output /tmp/television.sql
```

Expected output:

- it prints `Mode: UPDATE` (or `Mode: CREATE` if it does not exist yet)
- it writes `/tmp/television.sql`

---

## 6) Apply To ErsatzTV (Update DB + Reset Playout)

```bash
clickor apply --yaml /tmp/television.yaml \
  --apply \
  --mode replace
```

Expected output:

- `Apply succeeded.`
- `Resetting playout...`

---

## 7) Sanity Check: Playlist Item Count

This is a simple “did the DB change happen” confirmation:

```bash
clickor apply --yaml /tmp/television.yaml --dry-run
```

If you want to manually check counts in sqlite, do it on the host that has DB access.
