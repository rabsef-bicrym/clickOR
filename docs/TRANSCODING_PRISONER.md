# Transcoding: Fix The Prisoner (AV1) So QSV Works

This is a runbook for one very specific, very common problem:

- The Prisoner episodes are encoded as **AV1 + Opus** inside `.mkv`
- Your NUC is an **Intel i5-8259U (8th gen)**
- That CPU/iGPU does **not** have AV1 hardware decode
- A QSV hardware FFmpeg profile can fail on those episodes

The fix is to **re-encode the source files once** to something your Intel iGPU can decode:

- Video: H.264
- Audio: AAC
- Container: MKV

We do this *without changing filenames*, so your ErsatzTV configs/playlists do not need edits.

---

## What This Does (High Level)

1. Temporarily set Channel 1 (The Prisoner) to a software FFmpeg profile so playback won’t crash mid-job.
2. For each episode file:
   - run `ffmpeg` inside the `ersatztv` Docker container
   - write output to `/config/...` (writable)
   - atomically swap the new file into the original path on the host
   - store a backup of the original file
3. Force a scan of the Shows library so ErsatzTV refreshes codec metadata (paths didn’t change).
4. Set Channel 1 back to the QSV FFmpeg profile.

---

## Run It

From the Mac:

```bash
cd /path/to/ersatztv
source .venv/bin/activate

python clickor/bin/etv2_transcode_prisoner_inplace.py \
  --ssh "ssh -i ~/.ssh/your_key user@host"
```

Expected runtime:

- Roughly around an hour on the NUC (QSV encode + software AV1 decode).

Backups:

- The originals are kept under:
  - `/mnt/media/config/clickor-transcode/prisoner-YYYYMMDD-HHMMSS/backup`

---

## After It Finishes

1. Confirm channel profiles:
   - Channel 1 should be back on the QSV profile.
2. Confirm the Prisoner episode files are now H.264 + AAC:
   - (optional) spot-check with `ffprobe`

