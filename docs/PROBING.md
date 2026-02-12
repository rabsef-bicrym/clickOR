# Probing Durations (ffprobe)

clickOR configs require `duration_min` for every bumper and every content item.

There are two reliable ways to get durations:

1. Export from ErsatzTV DB (`clickor export-from-db`) if ErsatzTV has already scanned the media.
2. Probe directories directly (`clickor probe-dir`) using `ffprobe` over SSH.

This doc is about #2.

## When To Use probe-dir

Use `clickor probe-dir` when:

- ErsatzTV hasn’t scanned the files yet
- you want to build a config without touching ErsatzTV at all
- you want to double-check a directory’s durations

Note on performance:

- `probe-dir` runs `ffprobe` serially for correctness and portability.
- For very large directories, prefer `export-from-db` if ErsatzTV has already scanned media.

## Requirements

On the machine you SSH into:

- `find`
- `ffprobe` (usually installed with ffmpeg)

## Command

```bash
clickor probe-dir \
  --ssh "ssh -i /full/path/to/key user@host" \
  --dir "/mnt/media/other_videos/coronet" \
  --rewrite-prefix "/mnt/media=/media" \
  --type other_video \
  --out /tmp/coronet.items.json
```

Notes:

- `--dir` is the path as seen on the SSH host.
- `--rewrite-prefix` is explicit path rewriting:
  - If SSH sees `/mnt/media/...` but ErsatzTV uses `/media/...`, you must declare that mapping.
  - clickOR will not guess it.
- `--type` is the lineup type clickOR should emit for these paths.

Output format:

```json
{
  "items": [
    { "path": "/media/other_videos/coronet/...", "duration_min": 10.123, "type": "other_video" }
  ]
}
```

Copy that `items` list into your config:

- bumpers: `bumpers.pools.<pool_name>.items`
- content: `pools.<pool_name>.items`
