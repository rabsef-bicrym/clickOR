from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Optional

from .model import ChannelConfig, Item


class SolverError(Exception):
    pass


def _import_ortools():
    try:
        from ortools.sat.python import cp_model  # type: ignore
    except Exception as e:  # pragma: no cover
        raise SolverError(
            "OR-Tools is not installed.\n"
            "\n"
            "If you're using this repo directly:\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  python -m pip install -e .\n"
        ) from e
    return cp_model


@dataclass(frozen=True)
class SolvedBlock:
    index: int
    # Content items in this block, including repeats (as additional appearances of the same base item).
    items: list[Item]
    # True if this is a long-form solo block (>= target).
    is_long: bool

    # Debug stats
    base_items_count: int
    repeat_items_count: int
    content_duration_s: int
    waste_s: int


@dataclass(frozen=True)
class SolveResult:
    target_block_s: int
    blocks: list[SolvedBlock]
    # How many repeat placements were used.
    repeats_used: int
    # Total wasted seconds across short blocks.
    total_waste_s: int
    # Seed used for randomness in tie-breaking.
    seed: int


def _first_fit_decreasing_bins(short_items: list[Item], cap_s: int) -> list[list[int]]:
    """
    Greedy upper bound: First-Fit Decreasing bin packing.

    Returns bins as lists of indices into short_items.
    """
    order = sorted(range(len(short_items)), key=lambda i: short_items[i].duration_s, reverse=True)
    bins: list[list[int]] = []
    remaining: list[int] = []
    for i in order:
        d = short_items[i].duration_s
        placed = False
        for b in range(len(bins)):
            if remaining[b] >= d:
                bins[b].append(i)
                remaining[b] -= d
                placed = True
                break
        if not placed:
            bins.append([i])
            remaining.append(cap_s - d)
    return bins


def solve_minimal_cycle(cfg: ChannelConfig) -> SolveResult:
    """
    Solve for a minimal-length cycle:

    - Every base item appears exactly once.
    - Short items are packed into blocks with total <= block ceiling.
    - If longform_consumes_block is enabled, long items (>= block) are solo blocks.
    - After minimizing number of blocks, optional repeats may be inserted as filler
      (only for items marked repeatable), and diversity soft-penalties may be applied.
    """
    cp_model = _import_ortools()

    cap_s = cfg.solver.block_s
    ceiling_s = cap_s + cfg.solver.allow_short_overflow_s
    seed = cfg.solver.seed

    # Partition items.
    long_items: list[Item] = []
    short_items: list[Item] = []
    for it in cfg.items:
        if cfg.solver.longform_consumes_block and it.duration_s >= cap_s:
            long_items.append(it)
        else:
            short_items.append(it)

    # Upper bound for number of short blocks using greedy packing.
    greedy_bins = _first_fit_decreasing_bins(short_items, ceiling_s)
    ub_short = len(greedy_bins)
    ub_total = len(long_items) + ub_short
    if ub_total == 0:
        raise SolverError("No content items in config")

    # --- Phase 1: minimize total blocks (base items only) ---
    model = cp_model.CpModel()

    B = ub_total
    I_short = len(short_items)
    I_long = len(long_items)

    y = [model.NewBoolVar(f"y[{b}]") for b in range(B)]  # block used

    # Assign base short items to blocks.
    xs = [[model.NewBoolVar(f"x_short[{i},{b}]") for b in range(B)] for i in range(I_short)]
    # Assign long items to blocks (solo).
    xl = [[model.NewBoolVar(f"x_long[{l},{b}]") for b in range(B)] for l in range(I_long)]

    long_present = [model.NewBoolVar(f"long_present[{b}]") for b in range(B)]

    # Each base item appears exactly once.
    for i in range(I_short):
        model.Add(sum(xs[i][b] for b in range(B)) == 1)
    for l in range(I_long):
        model.Add(sum(xl[l][b] for b in range(B)) == 1)

    # At most one long item per block and define long_present.
    for b in range(B):
        model.Add(sum(xl[l][b] for l in range(I_long)) <= 1)
        if I_long:
            model.Add(long_present[b] == sum(xl[l][b] for l in range(I_long)))
        else:
            model.Add(long_present[b] == 0)

    # Capacity for short items and forbid short items in long blocks.
    for b in range(B):
        model.Add(sum(short_items[i].duration_s * xs[i][b] for i in range(I_short)) <= ceiling_s)
        # If a long item is present, no short items may be assigned.
        model.Add(sum(xs[i][b] for i in range(I_short)) == 0).OnlyEnforceIf(long_present[b])

    # Link usage variables.
    for b in range(B):
        for i in range(I_short):
            model.Add(xs[i][b] <= y[b])
        for l in range(I_long):
            model.Add(xl[l][b] <= y[b])
        model.Add(long_present[b] <= y[b])

    # Symmetry breaking: used blocks are a prefix.
    for b in range(B - 1):
        model.Add(y[b] >= y[b + 1])

    # Sequential (TV) ordering constraints:
    # For each sequential pool, enforce that episodes do not appear out of order by block index.
    short_idx_by_path = {it.path: i for i, it in enumerate(short_items)}
    long_idx_by_path = {it.path: l for l, it in enumerate(long_items)}

    # Compute block index IntVar per item (short + long).
    block_of_short = [model.NewIntVar(0, B - 1, f"block_of_short[{i}]") for i in range(I_short)]
    for i in range(I_short):
        model.Add(block_of_short[i] == sum(b * xs[i][b] for b in range(B)))

    block_of_long = [model.NewIntVar(0, B - 1, f"block_of_long[{l}]") for l in range(I_long)]
    for l in range(I_long):
        model.Add(block_of_long[l] == sum(b * xl[l][b] for b in range(B)))

    # Build ordered episode lists per pool.
    for pool_name, pool_cfg in cfg.pools.items():
        if not pool_cfg.sequential:
            continue
        eps = [it for it in cfg.items if it.pool == pool_name]
        eps_sorted = sorted(eps, key=lambda it: (it.season or 0, it.episode or 0, it.path))
        for a, b_item in zip(eps_sorted, eps_sorted[1:]):
            # a must be scheduled no later than b_item (nondecreasing blocks).
            if a.path in short_idx_by_path and b_item.path in short_idx_by_path:
                model.Add(block_of_short[short_idx_by_path[a.path]] <= block_of_short[short_idx_by_path[b_item.path]])
            elif a.path in short_idx_by_path and b_item.path in long_idx_by_path:
                model.Add(block_of_short[short_idx_by_path[a.path]] <= block_of_long[long_idx_by_path[b_item.path]])
            elif a.path in long_idx_by_path and b_item.path in short_idx_by_path:
                model.Add(block_of_long[long_idx_by_path[a.path]] <= block_of_short[short_idx_by_path[b_item.path]])
            elif a.path in long_idx_by_path and b_item.path in long_idx_by_path:
                model.Add(block_of_long[long_idx_by_path[a.path]] <= block_of_long[long_idx_by_path[b_item.path]])

    model.Minimize(sum(y))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(cfg.solver.time_limit_sec)
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverError("CP-SAT could not find a feasible schedule (base packing)")

    min_blocks = int(round(solver.ObjectiveValue()))

    # Extract a concrete feasible assignment to warm-start Phase 2.
    # This is important because Phase 2 adds a lot of extra structure (repeats, diversity),
    # and without a hint CP-SAT may spend most of its time just rediscovering feasibility.
    y_val = [int(solver.Value(y[b])) for b in range(B)]
    xs_val = [[int(solver.Value(xs[i][b])) for b in range(B)] for i in range(I_short)]
    xl_val = [[int(solver.Value(xl[l][b])) for b in range(B)] for l in range(I_long)] if I_long else []

    # --- Phase 2: fix minimal block count, add filler repeats and diversity objective ---
    model2 = cp_model.CpModel()

    y2 = [model2.NewBoolVar(f"y[{b}]") for b in range(B)]
    xs2 = [[model2.NewBoolVar(f"x_short[{i},{b}]") for b in range(B)] for i in range(I_short)]
    xl2 = [[model2.NewBoolVar(f"x_long[{l},{b}]") for b in range(B)] for l in range(I_long)]
    long_present2 = [model2.NewBoolVar(f"long_present[{b}]") for b in range(B)]

    for i in range(I_short):
        model2.Add(sum(xs2[i][b] for b in range(B)) == 1)
    for l in range(I_long):
        model2.Add(sum(xl2[l][b] for b in range(B)) == 1)

    for b in range(B):
        model2.Add(sum(xl2[l][b] for l in range(I_long)) <= 1)
        if I_long:
            model2.Add(long_present2[b] == sum(xl2[l][b] for l in range(I_long)))
        else:
            model2.Add(long_present2[b] == 0)

    for b in range(B):
        # Forbid short items in long blocks.
        model2.Add(sum(xs2[i][b] for i in range(I_short)) == 0).OnlyEnforceIf(long_present2[b])

    for b in range(B):
        for i in range(I_short):
            model2.Add(xs2[i][b] <= y2[b])
        for l in range(I_long):
            model2.Add(xl2[l][b] <= y2[b])
        model2.Add(long_present2[b] <= y2[b])

    for b in range(B - 1):
        model2.Add(y2[b] >= y2[b + 1])

    # Fix the minimal block count.
    model2.Add(sum(y2) == min_blocks)

    # Sequential (TV) ordering constraints again.
    block_of_short2 = [model2.NewIntVar(0, B - 1, f"block_of_short[{i}]") for i in range(I_short)]
    for i in range(I_short):
        model2.Add(block_of_short2[i] == sum(b * xs2[i][b] for b in range(B)))

    block_of_long2 = [model2.NewIntVar(0, B - 1, f"block_of_long[{l}]") for l in range(I_long)]
    for l in range(I_long):
        model2.Add(block_of_long2[l] == sum(b * xl2[l][b] for b in range(B)))

    for pool_name, pool_cfg in cfg.pools.items():
        if not pool_cfg.sequential:
            continue
        eps = [it for it in cfg.items if it.pool == pool_name]
        eps_sorted = sorted(eps, key=lambda it: (it.season or 0, it.episode or 0, it.path))
        for a, b_item in zip(eps_sorted, eps_sorted[1:]):
            if a.path in short_idx_by_path and b_item.path in short_idx_by_path:
                model2.Add(block_of_short2[short_idx_by_path[a.path]] <= block_of_short2[short_idx_by_path[b_item.path]])
            elif a.path in short_idx_by_path and b_item.path in long_idx_by_path:
                model2.Add(block_of_short2[short_idx_by_path[a.path]] <= block_of_long2[long_idx_by_path[b_item.path]])
            elif a.path in long_idx_by_path and b_item.path in short_idx_by_path:
                model2.Add(block_of_long2[long_idx_by_path[a.path]] <= block_of_short2[short_idx_by_path[b_item.path]])
            elif a.path in long_idx_by_path and b_item.path in long_idx_by_path:
                model2.Add(block_of_long2[long_idx_by_path[a.path]] <= block_of_long2[long_idx_by_path[b_item.path]])

    # Repeat variables (filler repeats for short items).
    r2 = [[model2.NewBoolVar(f"r[{i},{b}]") for b in range(B)] for i in range(I_short)]

    # Warm-start hints from Phase 1 solution.
    # Also hint all repeats to 0 initially.
    for b in range(B):
        model2.AddHint(y2[b], y_val[b])
        model2.AddHint(long_present2[b], 0)  # recalculated; safe default
    for i in range(I_short):
        for b in range(B):
            model2.AddHint(xs2[i][b], xs_val[i][b])
            model2.AddHint(r2[i][b], 0)
    for l in range(I_long):
        for b in range(B):
            model2.AddHint(xl2[l][b], xl_val[l][b])

    for i, it in enumerate(short_items):
        if not it.repeatable or it.max_extra_uses <= 0:
            for b in range(B):
                model2.Add(r2[i][b] == 0)
        else:
            model2.Add(sum(r2[i][b] for b in range(B)) <= it.max_extra_uses)

        # No repeats in long blocks.
        for b in range(B):
            model2.Add(r2[i][b] <= 1 - long_present2[b])

    # Capacity constraints for non-long blocks (base + repeats).
    used_short_time = [model2.NewIntVar(0, ceiling_s, f"used_short_time[{b}]") for b in range(B)]
    for b in range(B):
        # This sum is correct for non-long blocks; for long blocks, xs2=0 and r2 forced 0, so it becomes 0.
        model2.Add(
            used_short_time[b]
            == sum(short_items[i].duration_s * (xs2[i][b] + r2[i][b]) for i in range(I_short))
        )
        model2.Add(used_short_time[b] <= ceiling_s).OnlyEnforceIf(long_present2[b].Not())
        model2.Add(used_short_time[b] == 0).OnlyEnforceIf(long_present2[b])

    # Waste variables (only meaningful for non-long blocks).
    waste = [model2.NewIntVar(0, ceiling_s, f"waste[{b}]") for b in range(B)]
    for b in range(B):
        model2.Add(waste[b] == 0).OnlyEnforceIf(long_present2[b])
        model2.Add(waste[b] == ceiling_s - used_short_time[b]).OnlyEnforceIf(long_present2[b].Not())

    # Diversity: consecutive dominant blocks per pool.
    pool_names = list(cfg.pools.keys())
    pool_index = {p: idx for idx, p in enumerate(pool_names)}

    # Precompute which short items belong to each pool to keep constraints smaller.
    short_by_pool: dict[str, list[int]] = {p: [] for p in pool_names}
    for i, it in enumerate(short_items):
        short_by_pool[it.pool].append(i)

    long_by_pool: dict[str, list[int]] = {p: [] for p in pool_names}
    for l, it in enumerate(long_items):
        long_by_pool[it.pool].append(l)

    dominant = [[model2.NewBoolVar(f"dominant[{b},{p}]") for p in pool_names] for b in range(B)]
    consec_dom = [[model2.NewBoolVar(f"consec_dom[{b},{p}]") for p in pool_names] for b in range(B - 1)]

    for b in range(B):
        for p in pool_names:
            p_cfg = cfg.pools[p]
            # Pool time in this block = sum short durations + any long duration (long blocks are solo).
            # Upper bound for this IntVar: cap_s for short blocks, plus max long duration in pool.
            max_long = 0
            if long_by_pool[p]:
                max_long = max(long_items[l].duration_s for l in long_by_pool[p])
            pool_time = model2.NewIntVar(0, ceiling_s + max_long, f"pool_time[{b},{p}]")

            short_sum = sum(short_items[i].duration_s * (xs2[i][b] + r2[i][b]) for i in short_by_pool[p])
            long_sum = sum(long_items[l].duration_s * xl2[l][b] for l in long_by_pool[p])
            model2.Add(pool_time == short_sum + long_sum)

            thresh = p_cfg.dominant_block_threshold_s
            # If the pool has no diversity penalty, still define dominant so the model is consistent.
            model2.Add(pool_time >= thresh).OnlyEnforceIf(dominant[b][pool_index[p]])
            model2.Add(pool_time <= max(0, thresh - 1)).OnlyEnforceIf(dominant[b][pool_index[p]].Not())

    for b in range(B - 1):
        for p in pool_names:
            a = dominant[b][pool_index[p]]
            c = dominant[b + 1][pool_index[p]]
            d = consec_dom[b][pool_index[p]]
            model2.Add(d <= a)
            model2.Add(d <= c)
            model2.Add(d >= a + c - 1)

    # Objective terms:
    # - Minimize waste (seconds).
    # - Minimize repeat costs (seconds).
    # - Minimize diversity penalties (seconds).
    # - Add a tiny random tie-breaker so seeds produce different minimal solutions.
    obj_terms = []

    # Waste: only count waste for used blocks (y2=1) and non-long blocks.
    for b in range(B):
        # If y2[b]=0, waste is irrelevant; but symmetry makes them suffix anyway.
        # We still include waste; with y2 fixed, only prefix blocks matter.
        obj_terms.append(waste[b])

    # Repeat costs.
    for i, it in enumerate(short_items):
        if it.repeatable and it.repeat_cost_s > 0:
            for b in range(B):
                obj_terms.append(it.repeat_cost_s * r2[i][b])

    # Diversity penalties.
    for b in range(B - 1):
        for p in pool_names:
            pen = cfg.pools[p].dominant_block_penalty_s
            if pen <= 0:
                continue
            obj_terms.append(pen * consec_dom[b][pool_index[p]])

    # Random tie-breaker.
    rng = random.Random(seed)
    for i in range(I_short):
        for b in range(B):
            w = rng.randint(0, 3)  # small noise in seconds
            if w:
                obj_terms.append(w * xs2[i][b])

    model2.Minimize(sum(obj_terms))

    solver2 = cp_model.CpSolver()
    solver2.parameters.max_time_in_seconds = float(cfg.solver.time_limit_sec)
    solver2.parameters.random_seed = seed
    solver2.parameters.num_search_workers = 8

    status2 = solver2.Solve(model2)
    if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverError("CP-SAT could not find a feasible schedule (filler/diversity)")

    # Extract blocks.
    used_blocks = []
    for b in range(B):
        if solver2.Value(y2[b]) == 1:
            used_blocks.append(b)

    blocks: list[SolvedBlock] = []
    repeats_used = 0
    total_waste_s = 0

    for out_idx, b in enumerate(used_blocks):
        # Long item?
        long_in_block: Optional[Item] = None
        for l in range(I_long):
            if solver2.Value(xl2[l][b]) == 1:
                long_in_block = long_items[l]
                break

        base_items: list[Item] = []
        repeat_items: list[Item] = []
        for i in range(I_short):
            if solver2.Value(xs2[i][b]) == 1:
                base_items.append(short_items[i])
            if solver2.Value(r2[i][b]) == 1:
                repeat_items.append(short_items[i])
                repeats_used += 1

        is_long = long_in_block is not None
        items_in_block = ([long_in_block] if long_in_block else []) + base_items + repeat_items

        # Content duration accounting:
        if is_long:
            content_duration_s = long_in_block.duration_s  # type: ignore[union-attr]
            waste_s = 0
        else:
            content_duration_s = sum(it.duration_s for it in items_in_block)
            waste_s = ceiling_s - content_duration_s
            total_waste_s += waste_s

        blocks.append(
            SolvedBlock(
                index=out_idx,
                items=items_in_block,
                is_long=is_long,
                base_items_count=len(base_items) + (1 if long_in_block else 0),
                repeat_items_count=len(repeat_items),
                content_duration_s=content_duration_s,
                waste_s=waste_s,
            )
        )

    return SolveResult(
        target_block_s=cap_s,
        blocks=blocks,
        repeats_used=repeats_used,
        total_waste_s=total_waste_s,
        seed=seed,
    )
