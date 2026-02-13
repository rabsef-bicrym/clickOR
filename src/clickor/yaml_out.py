from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import yaml


@dataclass(frozen=True)
class PlaylistEntry:
    path: str
    media_type: str
    include_in_guide: bool = True


def build_yaml_config(
    *,
    channel: dict[str, Any],
    schedule: dict[str, Any],
    playlist_name: str,
    playlist_group: str,
    entries: Iterable[PlaylistEntry],
) -> dict[str, Any]:
    return {
        "channel": {
            "name": channel["name"],
            "number": channel["number"],
            "group": channel.get("group", channel["name"]),
        },
        "schedule": {
            "name": schedule.get("name", f"{channel['name']} Schedule"),
            "shuffle": bool(schedule.get("shuffle", False)),
            "guide_mode": schedule.get("guide_mode", "include_all"),
        },
        "playlist": {
            "name": playlist_name,
            "group": playlist_group,
            "items": [
                {
                    "path": e.path,
                    "type": e.media_type,
                    "include_in_guide": bool(e.include_in_guide),
                }
                for e in entries
            ],
        },
    }


def dump_yaml(obj: dict[str, Any], out_path: str) -> None:
    with open(out_path, "w") as f:
        yaml.dump(obj, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=200)
