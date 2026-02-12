from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .duration import DurationError, parse_hhmmss_to_seconds, seconds_to_minutes_float
from .remote_sqlite import RemoteSqliteError, Ssh, parse_ssh_prefix, run_sqlite


class ExportError(Exception):
    pass


ALLOWED_TYPES = {"episode", "movie", "music_video", "other_video"}


def _require(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise ExportError(f"Missing required key {key!r} in {where}")
    return obj[key]


def _read_json(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ExportError("Spec must be a JSON object")
    return raw


def _query_paths_under_prefixes(*, prefixes: list[str], db_path: str, ssh: Optional[Ssh], sudo: bool) -> list[dict[str, str]]:
    """
    Return rows: {path, duration, media_type}
    """
    if not prefixes:
        return []
    for p in prefixes:
        if not isinstance(p, str) or not p.startswith("/"):
            raise ExportError(f"Invalid prefix {p!r}. Prefixes must be absolute paths like /media/...")

    sql_lines = [
        "DROP TABLE IF EXISTS _clickor_prefixes;",
        "CREATE TEMP TABLE _clickor_prefixes(Prefix TEXT PRIMARY KEY);",
    ]
    for p in prefixes:
        esc = p.replace("'", "''")
        sql_lines.append(f"INSERT OR IGNORE INTO _clickor_prefixes(Prefix) VALUES ('{esc}');")

    sql_lines.append(
        """
SELECT DISTINCT
  mf.Path,
  v.Duration,
  CASE
    WHEN v.EpisodeId IS NOT NULL THEN 'episode'
    WHEN v.MovieId IS NOT NULL THEN 'movie'
    WHEN v.MusicVideoId IS NOT NULL THEN 'music_video'
    WHEN v.OtherVideoId IS NOT NULL THEN 'other_video'
    ELSE ''
  END as MediaType
FROM MediaFile mf
JOIN MediaVersion v ON v.Id = mf.MediaVersionId
WHERE EXISTS (
  SELECT 1 FROM _clickor_prefixes p
  WHERE mf.Path LIKE p.Prefix || '%'
)
;
""".strip()
    )

    out = run_sqlite(sql="\n".join(sql_lines) + "\n", db_path=db_path, ssh=ssh, sudo=sudo)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        path, dur, media_type = parts[0].strip(), parts[1].strip(), parts[2].strip()
        rows.append({"path": path, "duration": dur, "media_type": media_type})
    return rows


def _filter_rows(
    rows: list[dict[str, str]],
    *,
    only_types: Optional[list[str]] = None,
    include_contains: Optional[list[str]] = None,
    exclude_contains: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    out = []
    for r in rows:
        path = r["path"]
        mt = r["media_type"]

        if only_types is not None and mt not in only_types:
            continue

        if include_contains:
            ok = False
            for s in include_contains:
                if s in path:
                    ok = True
                    break
            if not ok:
                continue

        if exclude_contains:
            bad = False
            for s in exclude_contains:
                if s in path:
                    bad = True
                    break
            if bad:
                continue

        out.append(r)
    return out


def _rows_to_items(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for r in rows:
        mt = r["media_type"]
        if mt not in ALLOWED_TYPES:
            continue
        try:
            dur_s = parse_hhmmss_to_seconds(r["duration"])
        except DurationError as e:
            raise ExportError(f"Invalid duration for {r['path']}: {e}") from e
        if dur_s <= 0:
            raise ExportError(f"Duration is zero for {r['path']}. ErsatzTV may not have probed it.")
        items.append(
            {
                "path": r["path"],
                "duration_min": seconds_to_minutes_float(dur_s),
                "type": mt,
            }
        )
    items.sort(key=lambda x: x["path"])
    return items


def export_config_from_spec(
    *,
    spec_path: str,
    out_path: str,
    db_path: str,
    ssh_prefix: Optional[str],
    sudo: bool,
) -> None:
    """
    Read an export spec and write a concrete channel config JSON (ready for `clickor solve`).

    The spec is intentionally small and path-first: you describe pool prefixes, we query
    ErsatzTV for the exact MediaFile paths and durations it already knows.
    """
    spec = _read_json(spec_path)

    if ssh_prefix:
        try:
            ssh = parse_ssh_prefix(ssh_prefix)
        except RemoteSqliteError as e:
            raise ExportError(f"Invalid ssh prefix: {e}") from e
    else:
        ssh = None

    channel = spec.get("channel")
    if not isinstance(channel, dict):
        raise ExportError("Spec missing channel object")
    _require(channel, "name", "channel")
    _require(channel, "number", "channel")

    schedule = spec.get("schedule") or {}
    if not isinstance(schedule, dict):
        raise ExportError("Spec schedule must be an object if present")

    solver = spec.get("solver") or {}
    if not isinstance(solver, dict):
        raise ExportError("Spec solver must be an object if present")

    # ---- bumpers (pool prefixes -> concrete items) ----
    bumpers_spec = spec.get("bumpers")
    if not isinstance(bumpers_spec, dict):
        raise ExportError("Spec missing bumpers object")

    bumpers_out: dict[str, Any] = {
        "slots_per_break": int(bumpers_spec.get("slots_per_break", 1)),
        "mixing_strategy": bumpers_spec.get("mixing_strategy", "round_robin"),
        "pools": {},
    }

    pools_spec_b = bumpers_spec.get("pools")
    if not isinstance(pools_spec_b, dict) or not pools_spec_b:
        raise ExportError("Spec bumpers.pools must be a non-empty object mapping pool names to pool specs")

    for pool_name, pool_obj in pools_spec_b.items():
        if not isinstance(pool_obj, dict):
            raise ExportError(f"Spec bumpers.pools.{pool_name} must be an object")
        prefixes = pool_obj.get("include_path_prefixes") or []
        if not isinstance(prefixes, list):
            raise ExportError(f"Spec bumpers.pools.{pool_name}.include_path_prefixes must be a list")
        only_types = pool_obj.get("only_types")
        if only_types is not None and (not isinstance(only_types, list) or any(t not in ALLOWED_TYPES for t in only_types)):
            raise ExportError(f"Spec bumpers.pools.{pool_name}.only_types must be a list of allowed types")

        rows = _query_paths_under_prefixes(prefixes=prefixes, db_path=db_path, ssh=ssh, sudo=sudo)
        rows = _filter_rows(
            rows,
            only_types=only_types,
            include_contains=pool_obj.get("include_contains"),
            exclude_contains=pool_obj.get("exclude_contains"),
        )
        items = _rows_to_items(rows)
        weight = float(pool_obj.get("weight", 1.0))
        bumpers_out["pools"][pool_name] = {"weight": weight, "items": items}

    # ---- content pools ----
    pools_spec = spec.get("pools")
    if not isinstance(pools_spec, dict) or not pools_spec:
        raise ExportError("Spec pools must be a non-empty object mapping pool names to pool specs")

    pools_out: dict[str, Any] = {}
    type_counts = Counter()

    for pool_name, pool_obj in pools_spec.items():
        if not isinstance(pool_obj, dict):
            raise ExportError(f"Spec pools.{pool_name} must be an object")

        default_type = _require(pool_obj, "default_type", f"pools.{pool_name}")
        if default_type not in ALLOWED_TYPES:
            raise ExportError(f"Spec pools.{pool_name}.default_type must be one of {sorted(ALLOWED_TYPES)}")

        prefixes = pool_obj.get("include_path_prefixes") or []
        if not isinstance(prefixes, list):
            raise ExportError(f"Spec pools.{pool_name}.include_path_prefixes must be a list")

        only_types = pool_obj.get("only_types")
        if only_types is not None and (not isinstance(only_types, list) or any(t not in ALLOWED_TYPES for t in only_types)):
            raise ExportError(f"Spec pools.{pool_name}.only_types must be a list of allowed types")

        rows = _query_paths_under_prefixes(prefixes=prefixes, db_path=db_path, ssh=ssh, sudo=sudo)
        rows = _filter_rows(
            rows,
            only_types=only_types,
            include_contains=pool_obj.get("include_contains"),
            exclude_contains=pool_obj.get("exclude_contains"),
        )
        items = _rows_to_items(rows)

        overrides = pool_obj.get("overrides") or []
        if overrides and not isinstance(overrides, list):
            raise ExportError(f"Spec pools.{pool_name}.overrides must be a list if present")
        override_by_path = {}
        for o in overrides:
            if not isinstance(o, dict):
                continue
            p = o.get("path")
            if isinstance(p, str) and p:
                override_by_path[p] = o

        for it in items:
            type_counts[it["type"]] += 1
            ov = override_by_path.get(it["path"])
            if ov:
                # Only copy known keys, no guesswork.
                for k in ("repeatable", "repeat_cost_min", "max_extra_uses", "type"):
                    if k in ov:
                        it[k] = ov[k]

        pools_out[pool_name] = {
            "default_type": default_type,
            "sequential": bool(pool_obj.get("sequential", False)),
            "repeat": pool_obj.get("repeat", {}),
            "diversity": pool_obj.get("diversity", {}),
            "items": items,
        }

    out_obj = {
        "channel": channel,
        "schedule": schedule,
        "solver": {
            "block_minutes": solver.get("block_minutes", 30.0),
            "longform_consumes_block": solver.get("longform_consumes_block", True),
            "allow_short_overflow_minutes": solver.get("allow_short_overflow_minutes", 0.0),
            "time_limit_sec": solver.get("time_limit_sec", 60),
            # Default to auto (0) unless the user pins it in the spec.
            "seed": solver.get("seed", solver.get("random_seed", 0)),
        },
        "bumpers": bumpers_out,
        "pools": pools_out,
    }

    Path(out_path).write_text(json.dumps(out_obj, indent=2, ensure_ascii=False))
