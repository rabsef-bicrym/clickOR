from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

from .remote_sqlite import RemoteSqliteError, Ssh, run_sqlite


COLLECTION_TYPES: dict[str, int] = {
    "episode": 20,
    "movie": 10,
    "music_video": 30,
    "other_video": 40,
}


class BuilderError(Exception):
    pass


def load_yaml(path: str) -> dict[str, Any]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise BuilderError("YAML root must be a mapping/object")
    return raw


def _validate_sql_text(s: str, *, field: str) -> None:
    """
    Minimal safety checks for values we embed into SQL string literals.

    We still escape single quotes in _esc_sql (below), but we additionally reject
    NUL bytes because they can cause confusing truncation behavior across tools.

    This is not meant to be a full security boundary: clickOR assumes configs/YAML
    are trusted inputs you control.
    """
    if "\x00" in s:
        raise BuilderError(f"{field} contains a NUL byte (\\x00), which is not supported")


def _esc_sql(s: str) -> str:
    _validate_sql_text(s, field="sql text")
    return s.replace("'", "''")


@dataclass(frozen=True)
class ExistingIds:
    channel_id: Optional[int]
    playlist_id: Optional[int]
    schedule_id: Optional[int]
    playout_id: Optional[int]
    playlist_items_count: int
    playlist_max_index: int  # -1 when empty/missing


def check_existing(config: dict[str, Any], *, db_path: str, ssh: Optional[Ssh], sudo: bool) -> ExistingIds:
    ch_name = _esc_sql(config["channel"]["name"])
    pl_name = _esc_sql(config["playlist"]["name"])
    sched_name = _esc_sql(config.get("schedule", {}).get("name", f"{config['channel']['name']} Schedule"))

    # Everything in one round trip.
    sql = (
        f"SELECT 'channel', Id FROM Channel WHERE Name='{ch_name}'\n"
        f"UNION ALL SELECT 'playlist', Id FROM Playlist WHERE Name='{pl_name}'\n"
        f"UNION ALL SELECT 'schedule', Id FROM ProgramSchedule WHERE Name='{sched_name}'\n"
        f"UNION ALL SELECT 'playout', Id FROM Playout WHERE ChannelId=(SELECT Id FROM Channel WHERE Name='{ch_name}')\n"
        f"UNION ALL SELECT 'playlist_items', COUNT(*) FROM PlaylistItem WHERE PlaylistId=(SELECT Id FROM Playlist WHERE Name='{pl_name}')\n"
        f"UNION ALL SELECT 'playlist_max_index', COALESCE(MAX(\"Index\"), -1) FROM PlaylistItem WHERE PlaylistId=(SELECT Id FROM Playlist WHERE Name='{pl_name}')\n"
        f";\n"
    )

    out = run_sqlite(sql=sql, db_path=db_path, ssh=ssh, sudo=sudo)
    found: dict[str, int] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 2 and parts[1].strip():
            try:
                found[parts[0]] = int(parts[1])
            except ValueError:
                continue

    return ExistingIds(
        channel_id=found.get("channel"),
        playlist_id=found.get("playlist"),
        schedule_id=found.get("schedule"),
        playout_id=found.get("playout"),
        playlist_items_count=found.get("playlist_items", 0),
        playlist_max_index=found.get("playlist_max_index", -1),
    )


def resolve_media_ids(
    items: list[dict[str, Any]],
    *,
    db_path: str,
    ssh: Optional[Ssh],
    sudo: bool,
) -> tuple[list[tuple[int, int, Optional[int]]], list[str]]:
    """
    Resolve YAML playlist item paths to (MediaItemId, CollectionType, include_in_guide_override).

    This is strict by default: any unresolved path is an error.
    """
    sql_lines = [
        "DROP TABLE IF EXISTS _clickor_paths;",
        "CREATE TEMP TABLE _clickor_paths(Path TEXT PRIMARY KEY);",
    ]
    for it in items:
        p = it["path"]
        if not isinstance(p, str) or not p:
            raise BuilderError("playlist.items[].path must be a non-empty string")
        sql_lines.append(f"INSERT OR IGNORE INTO _clickor_paths(Path) VALUES ('{_esc_sql(p)}');")

    sql_lines.append(
        """
SELECT p.Path,
  COALESCE(e.Id, m.Id, mv2.Id, ov.Id) as MediaItemId,
  CASE
    WHEN e.Id IS NOT NULL THEN 20
    WHEN m.Id IS NOT NULL THEN 10
    WHEN mv2.Id IS NOT NULL THEN 30
    WHEN ov.Id IS NOT NULL THEN 40
  END as CollectionType
FROM _clickor_paths p
LEFT JOIN MediaFile mf ON mf.Path = p.Path
LEFT JOIN MediaVersion v ON v.Id = mf.MediaVersionId
LEFT JOIN Episode e ON e.Id = v.EpisodeId
LEFT JOIN Movie m ON m.Id = v.MovieId
LEFT JOIN MusicVideo mv2 ON mv2.Id = v.MusicVideoId
LEFT JOIN OtherVideo ov ON ov.Id = v.OtherVideoId
;
""".strip()
    )

    out = run_sqlite(sql="\n".join(sql_lines) + "\n", db_path=db_path, ssh=ssh, sudo=sudo)
    lookup: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        path = parts[0]
        mid = parts[1].strip()
        ctype = parts[2].strip()
        if not mid or not ctype:
            continue
        try:
            lookup[path] = (int(mid), int(ctype))
        except ValueError:
            continue

    resolved: list[tuple[int, int, Optional[int]]] = []
    errors: list[str] = []
    for idx, it in enumerate(items):
        path = it["path"]
        ty = it.get("type", "?")
        if path in lookup:
            include_raw = it.get("include_in_guide")
            include_override: Optional[int]
            if include_raw is None:
                include_override = None
            elif isinstance(include_raw, bool):
                include_override = 1 if include_raw else 0
            else:
                errors.append(
                    f"Item {idx}: include_in_guide must be boolean when provided for {ty} at {path}"
                )
                continue
            media_id, ctype = lookup[path]
            resolved.append((media_id, ctype, include_override))
        else:
            errors.append(f"Item {idx}: no match for {ty} at {path}")

    return resolved, errors


def generate_update_sql(
    config: dict[str, Any],
    resolved_items: list[tuple[int, int, Optional[int]]],
    existing: ExistingIds,
    *,
    mode: str,
) -> str:
    if existing.playlist_id is None:
        raise BuilderError("Cannot UPDATE without an existing PlaylistId")

    playlist_id = existing.playlist_id
    old_count = existing.playlist_items_count
    guide_mode = config.get("schedule", {}).get("guide_mode", "include_all")

    if mode not in ("replace", "append"):
        raise BuilderError("mode must be replace or append")

    sql: list[str] = []
    sql.append(f"-- clickOR: UPDATE {config['channel']['name']}")
    sql.append(f"-- Mode: {mode}")
    if mode == "replace":
        sql.append(f"-- Replacing {old_count} playlist items with {len(resolved_items)} new items")
        start_index = 0
    else:
        sql.append(f"-- Appending {len(resolved_items)} playlist items after existing {old_count}")
        start_index = existing.playlist_max_index + 1
    sql.append(f"-- Playlist ID: {playlist_id}")
    sql.append("")
    sql.append("BEGIN TRANSACTION;")
    sql.append("")

    if mode == "replace":
        sql.append("-- 1) Delete existing playlist items")
        sql.append(f"DELETE FROM PlaylistItem WHERE PlaylistId = {playlist_id};")
        sql.append("")
        sql.append(f"-- 2) Insert new playlist items ({len(resolved_items)} items, guide_mode={guide_mode})")
    else:
        sql.append(f"-- 1) Insert appended playlist items ({len(resolved_items)} items, guide_mode={guide_mode})")
        sql.append(f"-- Start index: {start_index}")

    for idx, (media_id, ctype, include_override) in enumerate(resolved_items):
        if include_override is None:
            guide = 1 if (guide_mode == "include_all" or ctype in (10, 20)) else 0
        else:
            guide = include_override
        sql.append(
            'INSERT INTO PlaylistItem ("Index", PlaylistId, CollectionType, CollectionId, MediaItemId, '
            "MultiCollectionId, SmartCollectionId, IncludeInProgramGuide, PlaybackOrder, PlayAll, Count) "
            f"VALUES ({start_index + idx}, {playlist_id}, {ctype}, NULL, {media_id}, NULL, NULL, {guide}, 0, 0, NULL);"
        )

    sql.append("")
    sql.append("COMMIT;")
    return "\n".join(sql) + "\n"


def generate_create_sql(config: dict[str, Any], resolved_items: list[tuple[int, int, Optional[int]]]) -> str:
    ch = config["channel"]
    pl = config["playlist"]
    sched = config.get("schedule", {})

    channel_name = ch["name"]
    channel_number = str(ch["number"])
    channel_group = ch.get("group", channel_name)
    playlist_name = pl["name"]
    playlist_group = pl.get("group", channel_name)
    schedule_name = sched.get("name", f"{channel_name} Schedule")
    shuffle = 1 if sched.get("shuffle", False) else 0
    channel_uuid = str(uuid.uuid4()).upper()
    guide_mode = sched.get("guide_mode", "include_all")

    sql: list[str] = []
    sql.append(f"-- clickOR: CREATE {channel_name}")
    sql.append(f"-- {len(resolved_items)} playlist items")
    sql.append("")
    sql.append("BEGIN TRANSACTION;")
    sql.append("")

    sql.append("-- 1) Playlist Group")
    sql.append(f"INSERT INTO PlaylistGroup (Name) VALUES ('{_esc_sql(playlist_group)}');")
    sql.append("")

    sql.append("-- 2) Playlist")
    sql.append(
        "INSERT INTO Playlist (IsSystem, Name, PlaylistGroupId) "
        f"VALUES (0, '{_esc_sql(playlist_name)}', (SELECT MAX(Id) FROM PlaylistGroup));"
    )
    sql.append("")

    sql.append(f"-- 3) Playlist Items ({len(resolved_items)} items, guide_mode={guide_mode})")
    for idx, (media_id, ctype, include_override) in enumerate(resolved_items):
        if include_override is None:
            guide = 1 if (guide_mode == "include_all" or ctype in (10, 20)) else 0
        else:
            guide = include_override
        sql.append(
            'INSERT INTO PlaylistItem ("Index", PlaylistId, CollectionType, CollectionId, MediaItemId, '
            "MultiCollectionId, SmartCollectionId, IncludeInProgramGuide, PlaybackOrder, PlayAll, Count) "
            f"VALUES ({idx}, (SELECT MAX(Id) FROM Playlist), {ctype}, NULL, {media_id}, NULL, NULL, {guide}, 0, 0, NULL);"
        )
    sql.append("")

    sql.append("-- 4) Schedule")
    sql.append(
        "INSERT INTO ProgramSchedule (FixedStartTimeBehavior, KeepMultiPartEpisodesTogether, "
        "Name, RandomStartPoint, ShuffleScheduleItems, TreatCollectionsAsShows) "
        f"VALUES (0, 0, '{_esc_sql(schedule_name)}', 0, {shuffle}, 0);"
    )
    sql.append("")

    sql.append("-- 5) Schedule Item (Flood)")
    sql.append(
        'INSERT INTO ProgramScheduleItem (CollectionType, PlaybackOrder, "Index", ProgramScheduleId, PlaylistId, '
        "FillWithGroupMode, GuideMode, MarathonGroupBy, MarathonShuffleGroups, MarathonShuffleItems) "
        "VALUES (6, 1, 0, (SELECT MAX(Id) FROM ProgramSchedule), "
        f"(SELECT Id FROM Playlist WHERE Name = '{_esc_sql(playlist_name)}'), "
        "0, 0, 0, 0, 0);"
    )
    sql.append("")

    sql.append("-- 6) Flood Item (CRITICAL - TPT inheritance)")
    sql.append("INSERT INTO ProgramScheduleFloodItem (Id) VALUES ((SELECT MAX(Id) FROM ProgramScheduleItem));")
    sql.append("")

    sql.append("-- 7) Channel")
    sql.append(
        'INSERT INTO Channel (Number, Name, UniqueId, FFmpegProfileId, '
        'StreamingMode, "Group", SortNumber, SubtitleMode, MusicVideoCreditsMode, '
        "IsEnabled, ShowInEpg, IdleBehavior, PlayoutMode, PlayoutSource, "
        "SongVideoMode, StreamSelectorMode, TranscodeMode) "
        f"VALUES ('{_esc_sql(channel_number)}', '{_esc_sql(channel_name)}', '{channel_uuid}', 1, "
        f"5, '{_esc_sql(channel_group)}', {float(channel_number)}, 3, 0, "
        "1, 1, 0, 0, 0, "
        "0, 0, 0);"
    )
    sql.append("")

    sql.append("-- 8) Playout")
    sql.append(
        "INSERT INTO Playout (ChannelId, ProgramScheduleId, ScheduleKind, Seed) "
        "VALUES ((SELECT Id FROM Channel WHERE Name = '{channel_name}'), "
        " (SELECT Id FROM ProgramSchedule WHERE Name = '{schedule_name}'), "
        " 1, 184984510);".format(
            channel_name=_esc_sql(channel_name),
            schedule_name=_esc_sql(schedule_name),
        )
    )
    sql.append("")

    sql.append("COMMIT;")
    return "\n".join(sql) + "\n"


def reset_playout(*, base_url: str, channel_number: str | int) -> None:
    """
    Request an ErsatzTV playout reset via HTTP.
    """
    base = base_url.rstrip("/")
    url = f"{base}/api/channels/{channel_number}/playout/reset"
    req = Request(url, method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except URLError as e:
        raise BuilderError(f"playout reset failed: {e}") from e


def dump_builder_report(
    *,
    yaml_path: str,
    existing: ExistingIds,
    resolved_count: int,
    total_items: int,
    mode: str,
    out_path: str,
) -> None:
    """
    Write a small JSON report that helps with debugging operational issues.
    """
    obj = {
        "yaml_path": yaml_path,
        "mode": mode,
        "existing": {
            "channel_id": existing.channel_id,
            "playlist_id": existing.playlist_id,
            "schedule_id": existing.schedule_id,
            "playout_id": existing.playout_id,
            "playlist_items_count": existing.playlist_items_count,
            "playlist_max_index": existing.playlist_max_index,
        },
        "resolved": {"count": resolved_count, "total": total_items},
    }
    with open(out_path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
