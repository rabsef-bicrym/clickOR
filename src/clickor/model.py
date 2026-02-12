from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


MediaType = str  # one of: episode|movie|music_video|other_video


@dataclass(frozen=True)
class BumperItem:
    """
    A bumper is any item that plays "between blocks".

    In your world this is often Coronet videos, station IDs, promos, etc.
    """

    path: str
    duration_s: int
    media_type: MediaType


@dataclass(frozen=True)
class BumperPoolConfig:
    """
    Configuration for one bumper pool.

    `items` are selected in an exhaust-before-repeat shuffle by default.
    """

    name: str
    weight: float
    items: list[BumperItem]


@dataclass(frozen=True)
class BumpersConfig:
    """
    Defines how bumpers are selected and inserted.

    The channel playlist is always:
      bumpers (N slots)
      content-block
      bumpers (N slots)
      content-block
      ...

    `slots_per_break` controls N.

    `mixing_strategy` controls which bumper pool supplies each slot:
      - "round_robin": cycle through pools in order
      - "weighted": choose a pool randomly by weight each slot
    """

    slots_per_break: int
    mixing_strategy: str
    pools: dict[str, BumperPoolConfig]


@dataclass(frozen=True)
class Item:
    # Canonical identity. Must match ErsatzTV MediaFile.Path exactly.
    path: str

    # Duration in seconds. The solver is integer-only.
    duration_s: int

    # Pool this item belongs to (e.g. "silly_symphony", "krtek").
    pool: str

    # Type to emit in YAML: episode|movie|music_video|other_video
    media_type: MediaType

    # Repeat policy (filler repeats within a single generated cycle).
    repeatable: bool
    repeat_cost_s: int
    max_extra_uses: int

    # Sequencing (TV).
    # If not None, these are used to enforce SxxExx ordering within the pool.
    season: Optional[int] = None
    episode: Optional[int] = None


@dataclass(frozen=True)
class PoolConfig:
    name: str
    default_type: MediaType
    sequential: bool

    # Repeat defaults
    default_repeatable: bool
    default_repeat_cost_s: int
    default_max_extra_uses: int

    # Diversity knobs (soft constraints)
    dominant_block_threshold_s: int  # if pool contributes >= this, block is "dominant" for this pool
    dominant_block_penalty_s: int    # penalty for adjacent dominant blocks


@dataclass(frozen=True)
class SolverConfig:
    block_s: int
    longform_consumes_block: bool
    allow_short_overflow_s: int
    time_limit_sec: int
    seed: int


@dataclass(frozen=True)
class ChannelConfig:
    channel: dict[str, Any]
    schedule: dict[str, Any]
    solver: SolverConfig
    bumpers: BumpersConfig
    pools: dict[str, PoolConfig]
    items: list[Item]
