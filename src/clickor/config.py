from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any, cast

from .model import (
    BumperItem,
    BumperPoolConfig,
    BumpersConfig,
    ChannelConfig,
    Item,
    PoolConfig,
    SolverConfig,
)
from .tv import parse_sxxexx


class ConfigError(Exception):
    pass


def parse_seed(value: Any, where: str) -> int:
    """
    Parse a seed value from config/CLI into an int seed.

    Accepted forms:
    - int
    - string (hashed deterministically)

    Convention:
    - seed=0 means "auto" (pick a random seed at runtime).
    """
    if value is None:
        return 0
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0
        # Allow explicit numeric strings.
        try:
            if s.startswith(("0x", "0X")):
                return int(s, 16)
            return int(s, 10)
        except ValueError:
            pass
        # Stable 32-bit hash.
        return int(zlib.crc32(s.encode("utf-8")) & 0x7FFFFFFF)
    raise ConfigError(f"Expected seed to be an int or string in {where}, got {type(value).__name__}")


def _require(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise ConfigError(f"Missing required key {key!r} in {where}")
    return obj[key]


def _as_int_seconds_minutes(value: Any, where: str) -> int:
    """
    Convert minutes (float/int) to seconds (int).
    """
    if not isinstance(value, (int, float)):
        raise ConfigError(f"Expected number of minutes in {where}, got {type(value).__name__}")
    if value < 0:
        raise ConfigError(f"Duration must be non-negative in {where}")
    return int(round(float(value) * 60.0))


def load_config(path: str | Path) -> ChannelConfig:
    path = Path(path)
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError("Top-level config must be a JSON object")

    channel = cast(dict[str, Any], raw.get("channel") or {})
    if not channel:
        raise ConfigError("Missing required top-level object: channel")
    _require(channel, "name", "channel")
    _require(channel, "number", "channel")

    schedule = cast(dict[str, Any], raw.get("schedule") or {})
    if not schedule:
        schedule = {
            "name": f"{channel['name']} Schedule",
            "shuffle": False,
            "guide_mode": "include_all",
        }
    else:
        schedule.setdefault("name", f"{channel['name']} Schedule")
        schedule.setdefault("shuffle", False)
        schedule.setdefault("guide_mode", "include_all")

    solver_raw = cast(dict[str, Any], raw.get("solver") or {})
    block_s = _as_int_seconds_minutes(solver_raw.get("block_minutes", 30.0), "solver.block_minutes")
    longform_consumes_block = bool(solver_raw.get("longform_consumes_block", True))
    allow_short_overflow_s = _as_int_seconds_minutes(
        solver_raw.get("allow_short_overflow_minutes", 0.0),
        "solver.allow_short_overflow_minutes",
    )
    time_limit_sec = int(solver_raw.get("time_limit_sec", 60))
    seed = parse_seed(solver_raw.get("random_seed", solver_raw.get("seed")), "solver.seed")
    solver = SolverConfig(
        block_s=block_s,
        longform_consumes_block=longform_consumes_block,
        allow_short_overflow_s=allow_short_overflow_s,
        time_limit_sec=time_limit_sec,
        seed=seed,
    )

    bumpers_raw = cast(dict[str, Any], raw.get("bumpers") or {})
    if not bumpers_raw:
        raise ConfigError("Missing required top-level object: bumpers")

    slots_per_break = int(bumpers_raw.get("slots_per_break", 1))
    if slots_per_break <= 0:
        raise ConfigError("bumpers.slots_per_break must be >= 1")

    mixing_strategy = bumpers_raw.get("mixing_strategy", "round_robin")
    if mixing_strategy not in ("round_robin", "weighted"):
        raise ConfigError("bumpers.mixing_strategy must be one of: round_robin, weighted")

    pools_raw_b = bumpers_raw.get("pools")
    if not isinstance(pools_raw_b, dict) or not pools_raw_b:
        raise ConfigError("bumpers.pools must be a non-empty object mapping pool names to pool configs")

    bumper_pools: dict[str, BumperPoolConfig] = {}
    for pool_name, pool_obj in pools_raw_b.items():
        if not isinstance(pool_name, str) or not pool_name:
            raise ConfigError("bumper pool names must be non-empty strings")
        if not isinstance(pool_obj, dict):
            raise ConfigError(f"bumpers.pools.{pool_name} must be an object")

        weight = float(pool_obj.get("weight", 1.0))
        items_raw = pool_obj.get("items")
        if not isinstance(items_raw, list) or not items_raw:
            raise ConfigError(f"bumpers.pools.{pool_name}.items must be a non-empty list")

        pool_items: list[BumperItem] = []
        for idx, it in enumerate(items_raw):
            where = f"bumpers.pools.{pool_name}.items[{idx}]"
            if not isinstance(it, dict):
                raise ConfigError(f"{where} must be an object")
            p = _require(it, "path", where)
            mt = it.get("type", "other_video")
            d = _as_int_seconds_minutes(_require(it, "duration_min", where), f"{where}.duration_min")
            if not isinstance(p, str) or not p:
                raise ConfigError(f"{where}.path must be a non-empty string")
            if not isinstance(mt, str):
                raise ConfigError(f"{where}.type must be a string")
            pool_items.append(BumperItem(path=p, duration_s=d, media_type=mt))

        bumper_pools[pool_name] = BumperPoolConfig(name=pool_name, weight=weight, items=pool_items)

    bumpers = BumpersConfig(
        slots_per_break=slots_per_break,
        mixing_strategy=str(mixing_strategy),
        pools=bumper_pools,
    )

    pools_raw = raw.get("pools")
    if not isinstance(pools_raw, dict) or not pools_raw:
        raise ConfigError("pools must be a non-empty object mapping pool names to pool configs")

    pools: dict[str, PoolConfig] = {}
    items: list[Item] = []

    for pool_name, pool_obj in pools_raw.items():
        if not isinstance(pool_name, str) or not pool_name:
            raise ConfigError("Pool names must be non-empty strings")
        if not isinstance(pool_obj, dict):
            raise ConfigError(f"Pool {pool_name!r} must be an object")

        default_type = pool_obj.get("default_type")
        if not isinstance(default_type, str) or not default_type:
            raise ConfigError(f"Pool {pool_name!r} missing required field default_type")

        sequential = bool(pool_obj.get("sequential", False))

        repeat_raw = cast(dict[str, Any], pool_obj.get("repeat") or {})
        default_repeatable = bool(repeat_raw.get("default_repeatable", False))
        default_repeat_cost_s = _as_int_seconds_minutes(
            repeat_raw.get("default_repeat_cost_min", 30),
            f"pools.{pool_name}.repeat.default_repeat_cost_min",
        )
        default_max_extra_uses = int(repeat_raw.get("default_max_extra_uses", 999))

        diversity_raw = cast(dict[str, Any], pool_obj.get("diversity") or {})
        dominant_block_threshold_s = _as_int_seconds_minutes(
            diversity_raw.get("dominant_block_threshold_min", 24),
            f"pools.{pool_name}.diversity.dominant_block_threshold_min",
        )
        dominant_block_penalty_s = _as_int_seconds_minutes(
            diversity_raw.get("dominant_block_penalty_min", 0),
            f"pools.{pool_name}.diversity.dominant_block_penalty_min",
        )

        pools[pool_name] = PoolConfig(
            name=pool_name,
            default_type=default_type,
            sequential=sequential,
            default_repeatable=default_repeatable,
            default_repeat_cost_s=default_repeat_cost_s,
            default_max_extra_uses=default_max_extra_uses,
            dominant_block_threshold_s=dominant_block_threshold_s,
            dominant_block_penalty_s=dominant_block_penalty_s,
        )

        pool_items = pool_obj.get("items")
        if not isinstance(pool_items, list) or not pool_items:
            raise ConfigError(f"Pool {pool_name!r}.items must be a non-empty list")

        for idx, it in enumerate(pool_items):
            where = f"pools.{pool_name}.items[{idx}]"
            if not isinstance(it, dict):
                raise ConfigError(f"{where} must be an object")

            p = _require(it, "path", where)
            if not isinstance(p, str) or not p:
                raise ConfigError(f"{where}.path must be a non-empty string")

            d = _as_int_seconds_minutes(_require(it, "duration_min", where), f"{where}.duration_min")

            mt = it.get("type", default_type)
            if not isinstance(mt, str) or not mt:
                raise ConfigError(f"{where}.type must be a non-empty string")

            repeatable = bool(it.get("repeatable", default_repeatable))
            if "repeat_cost_min" in it:
                repeat_cost_s = _as_int_seconds_minutes(it["repeat_cost_min"], f"{where}.repeat_cost_min")
            else:
                repeat_cost_s = default_repeat_cost_s
            max_extra_uses = int(it.get("max_extra_uses", default_max_extra_uses))

            season = None
            episode = None
            if sequential:
                eid = parse_sxxexx(p)
                if eid is None:
                    raise ConfigError(
                        f"{where} is in a sequential pool but does not contain an SxxExx pattern: {p}"
                    )
                season = eid.season
                episode = eid.episode

            items.append(
                Item(
                    path=p,
                    duration_s=d,
                    pool=pool_name,
                    media_type=mt,
                    repeatable=repeatable,
                    repeat_cost_s=repeat_cost_s,
                    max_extra_uses=max_extra_uses,
                    season=season,
                    episode=episode,
                )
            )

    # Basic sanity: ensure no duplicate base paths across pools.
    seen: set[str] = set()
    dups: list[str] = []
    for it in items:
        if it.path in seen:
            dups.append(it.path)
        seen.add(it.path)
    if dups:
        # Duplicate base paths is almost always a config error.
        # If you really want duplicates, use repeats, not duplicated base entries.
        raise ConfigError(f"Duplicate item paths found in config (base items must be unique): {dups[:5]}")

    return ChannelConfig(
        channel=channel,
        schedule=schedule,
        solver=solver,
        bumpers=bumpers,
        pools=pools,
        items=items,
    )
