from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, Sequence

from .model import BumperItem, BumperPoolConfig, BumpersConfig


@dataclass
class _ExhaustShuffleCycler:
    """
    Exhaust-before-repeat shuffler for a single pool.

    Extra guard:
    - Avoid repeating the same path across shuffle boundaries if possible.
    """

    items: Sequence[BumperItem]
    seed: int

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("bumper pool requires at least one item")
        self._rng = random.Random(self.seed)
        self._bag: list[BumperItem] = []
        self._last_path: str | None = None

    def _refill(self) -> None:
        bag = list(self.items)
        self._rng.shuffle(bag)
        if self._last_path is not None and len(bag) > 1 and bag[0].path == self._last_path:
            bag = bag[1:] + bag[:1]
        self._bag = bag

    def next(self) -> BumperItem:
        if not self._bag:
            self._refill()
        it = self._bag.pop(0)
        self._last_path = it.path
        return it


@dataclass
class BumperSelector:
    """
    Select bumpers for each break (each "between blocks" region).

    This is intentionally not part of CP-SAT. It is a deterministic, debuggable
    post-processing step driven by config.
    """

    cfg: BumpersConfig
    seed: int

    def __post_init__(self) -> None:
        if self.cfg.slots_per_break <= 0:
            raise ValueError("bumpers.slots_per_break must be >= 1")
        if not self.cfg.pools:
            raise ValueError("bumpers.pools must be non-empty")

        self._pool_names = list(self.cfg.pools.keys())
        self._rr_index = 0
        self._rng = random.Random(self.seed ^ 0xA5A5A5A5)

        self._cyclers: dict[str, _ExhaustShuffleCycler] = {}
        for name, pool in self.cfg.pools.items():
            # Mix pool-local randomness with global seed to keep deterministic behavior.
            pool_seed = self.seed ^ (hash(name) & 0xFFFFFFFF)
            self._cyclers[name] = _ExhaustShuffleCycler(pool.items, seed=pool_seed)

    def _choose_pool_name(self) -> str:
        strategy = self.cfg.mixing_strategy
        if strategy == "round_robin":
            name = self._pool_names[self._rr_index % len(self._pool_names)]
            self._rr_index += 1
            return name
        if strategy == "weighted":
            # Weighted choice each slot; each pool still exhaust-shuffles internally.
            names: list[str] = []
            weights: list[float] = []
            for name, pool in self.cfg.pools.items():
                names.append(name)
                weights.append(max(float(pool.weight), 0.0))
            if sum(weights) <= 0:
                # All weights zero; fall back to rr.
                name = self._pool_names[self._rr_index % len(self._pool_names)]
                self._rr_index += 1
                return name
            return self._rng.choices(names, weights=weights, k=1)[0]
        raise ValueError(f"Unknown bumpers.mixing_strategy: {strategy!r}")

    def next_bumpers(self) -> list[BumperItem]:
        """
        Return the bumper items for one break (a list of length slots_per_break).
        """
        out: list[BumperItem] = []
        for _ in range(self.cfg.slots_per_break):
            pool = self._choose_pool_name()
            out.append(self._cyclers[pool].next())
        return out

    def iter_bumpers(self) -> Iterator[list[BumperItem]]:
        while True:
            yield self.next_bumpers()


def default_bumpers_config(*, items: list[BumperItem], slots_per_break: int = 1) -> BumpersConfig:
    """
    Convenience helper: treat a single list of items as one bumper pool.
    """
    pool = BumperPoolConfig(name="default", weight=1.0, items=items)
    return BumpersConfig(slots_per_break=slots_per_break, mixing_strategy="round_robin", pools={"default": pool})

