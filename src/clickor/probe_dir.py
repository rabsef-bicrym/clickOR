from __future__ import annotations

import json
import shlex
import subprocess
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional


class ProbeError(Exception):
    pass


def _rewrite(path: str, rewrite_prefix: str | None) -> str:
    if not rewrite_prefix:
        return path
    if "=" not in rewrite_prefix:
        raise ProbeError("--rewrite-prefix must look like FROM=TO")
    frm, to = rewrite_prefix.split("=", 1)
    if path.startswith(frm):
        return to + path[len(frm) :]
    return path


@dataclass(frozen=True)
class ProbeResultItem:
    path: str
    duration_min: float
    media_type: str


def probe_dir_over_ssh(
    *,
    ssh_prefix: str,
    remote_dir: str,
    rewrite_prefix: Optional[str],
    media_type: str,
    exts: list[str],
) -> list[ProbeResultItem]:
    """
    Probe media files in a remote directory over SSH using ffprobe.

    Returns a list of (path, duration_min, type) items, sorted by path.
    """
    exts2 = [e.lower().lstrip(".") for e in exts]
    if not exts2:
        raise ProbeError("At least one --ext is required")

    # Basic path sanity: remote_dir should look like an absolute posix path.
    _ = PurePosixPath(remote_dir)
    if not remote_dir.startswith("/"):
        raise ProbeError("--dir must be an absolute path on the remote host (like /mnt/media/...)")

    # Build a remote shell script that outputs: duration_seconds|absolute_path
    # Keep it serial for correctness.
    dirq = shlex.quote(remote_dir)
    find_expr = " -o ".join([f"-name {shlex.quote(f'*.{e}')}" for e in exts2])
    remote_script = f"""
set -e
find {dirq} -type f \\( {find_expr} \\) | while IFS= read -r f; do
  dur=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || true)
  echo "$dur|$f"
done
""".strip()

    # ssh_prefix is expected to be something like: ssh -i ~/.ssh/key user@host
    ssh_argv = [os.path.expanduser(a) for a in shlex.split(ssh_prefix)]
    cmd = ssh_argv + ["sh", "-lc", remote_script]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ProbeError(f"SSH command failed: {(r.stderr or '').strip()}")

    items: list[ProbeResultItem] = []
    for line in (r.stdout or "").splitlines():
        if "|" not in line:
            continue
        dur_s_raw, path = line.split("|", 1)
        dur_s_raw = dur_s_raw.strip()
        path = path.strip()
        if not dur_s_raw:
            continue
        try:
            dur_s = float(dur_s_raw)
        except ValueError:
            continue
        dur_min = round(dur_s / 60.0, 3)
        out_path = _rewrite(path, rewrite_prefix)
        items.append(ProbeResultItem(path=out_path, duration_min=dur_min, media_type=media_type))

    items.sort(key=lambda x: x.path)
    return items


def write_probe_json(*, items: list[ProbeResultItem], out_path: str) -> None:
    obj = {
        "items": [{"path": it.path, "duration_min": it.duration_min, "type": it.media_type} for it in items],
    }
    Path(out_path).write_text(json.dumps(obj, indent=2, ensure_ascii=False))
