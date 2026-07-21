from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from .bumpers import BumperSelector
from .companions import cards_for_item
from .config import parse_seed
from .model import ChannelConfig
from .solver import SolveResult, solve_minimal_cycle
from .yaml_out import PlaylistEntry, build_yaml_config


class GenerateError(Exception):
    pass


def block_entries_with_companions(rules, block_items) -> list[PlaylistEntry]:
    """
    One content block's playlist entries: each item wrapped with any companion
    cards it is owed. The first item of the block also triggers block_start
    rules. Pure and solver-free so it can be tested directly.
    """
    entries: list[PlaylistEntry] = []
    for pos, it in enumerate(block_items):
        cards = cards_for_item(
            rules,
            path=it.path,
            pool=it.pool,
            media_type=it.media_type,
            is_block_start=(pos == 0),
        )
        for c in cards:
            if c.position == "before":
                entries.append(
                    PlaylistEntry(path=c.path, media_type=c.media_type, include_in_guide=c.include_in_guide)
                )
        entries.append(PlaylistEntry(path=it.path, media_type=it.media_type))
        for c in cards:
            if c.position == "after":
                entries.append(
                    PlaylistEntry(path=c.path, media_type=c.media_type, include_in_guide=c.include_in_guide)
                )
    return entries


def _apply_solver_overrides(
    cfg: ChannelConfig,
    *,
    seed_override: Optional[str | int],
    time_limit_sec: Optional[int],
    block_minutes: Optional[float],
    allow_short_overflow_minutes: Optional[float],
    longform_consumes_block: Optional[bool],
) -> ChannelConfig:
    solver = cfg.solver

    if seed_override is not None:
        seed = parse_seed(seed_override, "cli.seed")
        solver = replace(solver, seed=seed)

    if time_limit_sec is not None:
        solver = replace(solver, time_limit_sec=int(time_limit_sec))

    if block_minutes is not None:
        solver = replace(solver, block_s=int(round(float(block_minutes) * 60.0)))

    if allow_short_overflow_minutes is not None:
        solver = replace(solver, allow_short_overflow_s=int(round(float(allow_short_overflow_minutes) * 60.0)))

    if longform_consumes_block is not None:
        solver = replace(solver, longform_consumes_block=bool(longform_consumes_block))

    return replace(cfg, solver=solver)


def solve_to_yaml_obj(
    cfg: ChannelConfig,
    *,
    playlist_name: str,
    playlist_group: str,
    seed_override: Optional[str | int] = None,
    time_limit_sec: Optional[int] = None,
    block_minutes: Optional[float] = None,
    allow_short_overflow_minutes: Optional[float] = None,
    longform_consumes_block: Optional[bool] = None,
) -> tuple[dict[str, Any], SolveResult]:
    """
    Solve, then convert to an ErsatzTV playlist YAML object (dict).
    """
    cfg2 = _apply_solver_overrides(
        cfg,
        seed_override=seed_override,
        time_limit_sec=time_limit_sec,
        block_minutes=block_minutes,
        allow_short_overflow_minutes=allow_short_overflow_minutes,
        longform_consumes_block=longform_consumes_block,
    )

    # Convention: seed=0 means auto; the CLI should have replaced it already.
    if cfg2.solver.seed == 0:
        raise GenerateError("cfg.solver.seed is 0 (auto) but was not replaced with a concrete seed")

    result = solve_minimal_cycle(cfg2)

    selector = BumperSelector(cfg2.bumpers, seed=result.seed)
    entries: list[PlaylistEntry] = []
    for block in result.blocks:
        bumpers = selector.next_bumpers()
        entries.extend([PlaylistEntry(path=b.path, media_type=b.media_type) for b in bumpers])
        entries.extend(block_entries_with_companions(cfg2.companions, block.items))

    yaml_obj = build_yaml_config(
        channel=cfg2.channel,
        schedule=cfg2.schedule,
        playlist_name=playlist_name,
        playlist_group=playlist_group,
        entries=entries,
    )
    return yaml_obj, result

