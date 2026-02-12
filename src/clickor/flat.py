from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .yaml_out import PlaylistEntry


class FlatError(Exception):
    pass


def _require(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise FlatError(f"Missing required key {key!r} in {where}")
    return obj[key]


def _as_non_empty_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlatError(f"Expected non-empty string in {where}")
    return value


def _as_optional_int_seconds(value: Any, where: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise FlatError(f"Expected number (seconds) in {where}, got {type(value).__name__}")
    if float(value) < 0:
        raise FlatError(f"Expected non-negative seconds in {where}")
    return int(round(float(value)))


def probe_duration_seconds(path: str) -> float:
    """
    Return a media file duration in seconds using ffprobe.

    This is used by `clickor flat` to compute repeat counts for short bumpers.
    """
    # Keep argv form; avoid shell quoting issues.
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        raise FlatError(f"ffprobe failed for {path!r}: {stderr or 'unknown error'}")
    raw = (r.stdout or "").strip()
    if not raw:
        raise FlatError(f"ffprobe returned empty duration for {path!r}")
    try:
        dur_s = float(raw)
    except ValueError as e:
        raise FlatError(f"ffprobe returned non-numeric duration for {path!r}: {raw!r}") from e
    if dur_s <= 0:
        raise FlatError(f"ffprobe returned non-positive duration for {path!r}: {dur_s}")
    return dur_s


@dataclass(frozen=True)
class FlatShortLoopConfig:
    under_s: int
    loop_to_s: int  # 0 disables auto-looping


@dataclass(frozen=True)
class FlatItem:
    item_type: str
    path: str
    loop_to_s: Optional[int]
    auto_loop: bool


@dataclass(frozen=True)
class FlatConfig:
    channel_name: str
    channel_number: Optional[int]
    channel_group: Optional[str]
    playlist_name: str
    playlist_group: str
    schedule_name: str
    schedule_shuffle: bool
    schedule_guide_mode: str
    short_loop: FlatShortLoopConfig
    items: list[FlatItem]


_ALLOWED_MEDIA_TYPES = {"episode", "movie", "music_video", "other_video"}
_TYPE_TO_MEDIA_TYPE = {
    # "Solve" schema types.
    "episode": "episode",
    "movie": "movie",
    "music_video": "music_video",
    "other_video": "other_video",
    # Flat schema shorthands.
    "feature": "movie",
    "bumper": "other_video",
    "interstitial": "other_video",
}


def load_flat_config(path: str | Path) -> FlatConfig:
    p = Path(path)
    raw = json.loads(p.read_text())
    if not isinstance(raw, dict):
        raise FlatError("Top-level flat config must be a JSON object")

    mode = raw.get("mode", "flat")
    if mode != "flat":
        raise FlatError(f"flat config mode must be 'flat', got {mode!r}")

    channel_name = _as_non_empty_str(_require(raw, "channel_name", "root"), "channel_name")
    channel_number_raw = raw.get("channel_number")
    channel_number = None
    if channel_number_raw is not None:
        if not isinstance(channel_number_raw, int) or channel_number_raw <= 0:
            raise FlatError("channel_number must be a positive int when provided")
        channel_number = int(channel_number_raw)

    channel_group_raw = raw.get("channel_group")
    channel_group = None
    if channel_group_raw is not None:
        channel_group = _as_non_empty_str(channel_group_raw, "channel_group")

    playlist_name = str(raw.get("playlist_name") or f"{channel_name} Playlist")
    playlist_group = str(raw.get("playlist_group") or channel_name)
    schedule_name = str(raw.get("schedule_name") or f"{channel_name} Schedule")
    schedule_shuffle = bool(raw.get("schedule_shuffle", False))
    schedule_guide_mode = str(raw.get("schedule_guide_mode") or "include_all")

    short_under = _as_optional_int_seconds(raw.get("loop_short_under"), "loop_short_under")
    short_to = _as_optional_int_seconds(raw.get("loop_short_to"), "loop_short_to")
    short_loop = FlatShortLoopConfig(
        under_s=15 if short_under is None else int(short_under),
        loop_to_s=30 if short_to is None else int(short_to),
    )
    if short_loop.under_s < 0:
        raise FlatError("loop_short_under must be >= 0")
    if short_loop.loop_to_s < 0:
        raise FlatError("loop_short_to must be >= 0")

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise FlatError("items must be a non-empty list")

    items: list[FlatItem] = []
    for idx, it in enumerate(items_raw):
        where = f"items[{idx}]"
        if not isinstance(it, dict):
            raise FlatError(f"{where} must be an object")
        item_type = str(it.get("type") or "other_video")
        if item_type not in _TYPE_TO_MEDIA_TYPE:
            raise FlatError(
                f"{where}.type must be one of {sorted(_TYPE_TO_MEDIA_TYPE.keys())}, got {item_type!r}"
            )
        path_s = _as_non_empty_str(_require(it, "path", where), f"{where}.path")
        loop_to_s = _as_optional_int_seconds(it.get("loop_to"), f"{where}.loop_to")
        auto_loop = it.get("auto_loop", True)
        if not isinstance(auto_loop, bool):
            raise FlatError(f"{where}.auto_loop must be a boolean when provided")
        items.append(
            FlatItem(item_type=item_type, path=path_s, loop_to_s=loop_to_s, auto_loop=auto_loop)
        )

    return FlatConfig(
        channel_name=channel_name,
        channel_number=channel_number,
        channel_group=channel_group,
        playlist_name=playlist_name,
        playlist_group=playlist_group,
        schedule_name=schedule_name,
        schedule_shuffle=schedule_shuffle,
        schedule_guide_mode=schedule_guide_mode,
        short_loop=short_loop,
        items=items,
    )


def _repeat_count(*, duration_s: float, target_s: int) -> int:
    if target_s <= 0:
        return 1
    if duration_s <= 0:
        raise FlatError("duration must be > 0 to compute repeats")
    return max(1, int(math.ceil(float(target_s) / float(duration_s))))


def expand_flat_to_playlist_entries(
    cfg: FlatConfig,
    *,
    probe: Callable[[str], float] = probe_duration_seconds,
) -> list[PlaylistEntry]:
    """
    Expand flat items to a linear playlist (with repeats for short-loop items).
    """
    entries: list[PlaylistEntry] = []

    for it in cfg.items:
        media_type = _TYPE_TO_MEDIA_TYPE[it.item_type]
        if media_type not in _ALLOWED_MEDIA_TYPES:
            raise FlatError(f"BUG: mapped media_type {media_type!r} is not allowed")

        target_s: Optional[int] = it.loop_to_s
        if target_s is None and it.auto_loop and cfg.short_loop.loop_to_s > 0:
            dur_s = probe(it.path)
            if dur_s < float(cfg.short_loop.under_s):
                target_s = cfg.short_loop.loop_to_s
        elif target_s is not None:
            dur_s = probe(it.path)

        if target_s is None:
            n = 1
        else:
            n = _repeat_count(duration_s=dur_s, target_s=int(target_s))

        for _ in range(n):
            entries.append(PlaylistEntry(path=it.path, media_type=media_type))

    return entries


def build_lineup_config_for_db(cfg: FlatConfig, *, items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build the ersatztv_db-compatible config object (like the lineup YAML root).

    Notes:
    - For UPDATE SQL, only channel.name, playlist.name, and schedule.guide_mode are required.
    - For CREATE SQL, channel.number must be present.
    """
    ch: dict[str, Any] = {"name": cfg.channel_name}
    if cfg.channel_number is not None:
        ch["number"] = cfg.channel_number
    if cfg.channel_group is not None:
        ch["group"] = cfg.channel_group

    return {
        "channel": ch,
        "schedule": {
            "name": cfg.schedule_name,
            "shuffle": bool(cfg.schedule_shuffle),
            "guide_mode": cfg.schedule_guide_mode,
        },
        "playlist": {
            "name": cfg.playlist_name,
            "group": cfg.playlist_group,
            "items": items,
        },
    }
