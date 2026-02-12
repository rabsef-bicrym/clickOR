from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import yaml

from .model import ChannelConfig
from .tv import parse_sxxexx


class VerifyError(Exception):
    pass


@dataclass(frozen=True)
class VerifyFinding:
    level: str  # "ERROR" | "WARN"
    message: str


def _load_yaml_items(yaml_path: str) -> list[dict[str, Any]]:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise VerifyError("YAML must be a mapping/object at the top level")
    pl = raw.get("playlist")
    if not isinstance(pl, dict):
        raise VerifyError("YAML missing top-level 'playlist' object")
    items = pl.get("items")
    if not isinstance(items, list):
        raise VerifyError("YAML playlist.items must be a list")
    out: list[dict[str, Any]] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            raise VerifyError(f"YAML playlist.items[{idx}] must be an object")
        if "path" not in it:
            raise VerifyError(f"YAML playlist.items[{idx}] missing 'path'")
        out.append(it)
    return out


def verify_yaml_against_config(cfg: ChannelConfig, yaml_path: str) -> list[VerifyFinding]:
    """
    Verify a generated YAML lineup against the config that was used to generate it.

    This is intentionally "dumb and strict".
    If something looks weird, it should be caught here before touching ErsatzTV.
    """
    findings: list[VerifyFinding] = []

    items = _load_yaml_items(yaml_path)
    if not items:
        return [VerifyFinding("ERROR", "YAML playlist.items is empty")]

    # Build lookup maps from config.
    bumper_paths: list[str] = []
    bumper_pool_by_path: dict[str, str] = {}
    for pool_name, pool in cfg.bumpers.pools.items():
        for it in pool.items:
            bumper_paths.append(it.path)
            bumper_pool_by_path[it.path] = pool_name

    bumper_set = set(bumper_paths)
    if len(bumper_set) != len(bumper_paths):
        findings.append(VerifyFinding("ERROR", "Config bumpers contain duplicate paths across pools"))

    content_by_path = {it.path: it for it in cfg.items}
    duration_by_path_s: dict[str, int] = {it.path: it.duration_s for it in cfg.items}
    for pool in cfg.bumpers.pools.values():
        duration_by_path_s.update({it.path: it.duration_s for it in pool.items})

    # 1) Playlist should start with bumpers.
    slots = cfg.bumpers.slots_per_break
    if len(items) < slots + 1:
        findings.append(
            VerifyFinding(
                "ERROR",
                f"Playlist is too short to contain even one full break of {slots} bumper(s) + content",
            )
        )
        return findings

    for i in range(slots):
        p = items[i]["path"]
        if p not in bumper_set:
            findings.append(
                VerifyFinding(
                    "ERROR",
                    f"Playlist does not start with {slots} bumper(s). Item {i} is not a bumper: {p}",
                )
            )
            break

    # 2) Enforce the bumper/content alternating run structure.
    # Required pattern is:
    #   bumpers (exactly N items)
    #   content (>= 1 item)
    #   bumpers (exactly N items)
    #   content (>= 1 item)
    #
    # And because Flood loops: the playlist should end with content (not bumpers).
    def is_bumper(path: str) -> bool:
        return path in bumper_set

    runs: list[tuple[bool, int, int]] = []  # (is_bumper, length, start_index)
    cur_is_b = is_bumper(items[0]["path"])
    cur_len = 0
    cur_start = 0
    for idx, it in enumerate(items):
        b = is_bumper(it["path"])
        if b == cur_is_b:
            cur_len += 1
        else:
            runs.append((cur_is_b, cur_len, cur_start))
            cur_is_b = b
            cur_len = 1
            cur_start = idx
    runs.append((cur_is_b, cur_len, cur_start))

    if not runs or not runs[0][0]:
        findings.append(VerifyFinding("ERROR", "Playlist does not start with bumpers"))
    if runs and runs[-1][0]:
        findings.append(VerifyFinding("ERROR", "Playlist ends with bumpers; Flood wrap will create a bumper run boundary issue"))

    for is_b, ln, start in runs:
        if is_b and ln != slots:
            findings.append(
                VerifyFinding(
                    "ERROR",
                    f"Bumper run length must be exactly {slots}. Found run length {ln} starting at index {start}",
                )
            )
            break
        if (not is_b) and ln <= 0:
            findings.append(VerifyFinding("ERROR", f"Empty content run at index {start}"))
            break

    # 3) All paths should be known (either bumper or content).
    unknown = []
    for idx, it in enumerate(items):
        p = it["path"]
        if p not in bumper_set and p not in content_by_path:
            unknown.append((idx, p))
    if unknown:
        findings.append(
            VerifyFinding(
                "ERROR",
                f"YAML contains paths not present in config. First few: {unknown[:5]}",
            )
        )

    # 4) Repeats policy.
    counts: dict[str, int] = {}
    for it in items:
        p = it["path"]
        if p in bumper_set:
            continue
        counts[p] = counts.get(p, 0) + 1

    missing = [p for p in content_by_path.keys() if counts.get(p, 0) == 0]
    if missing:
        findings.append(VerifyFinding("ERROR", f"Missing base content items (should appear at least once): {missing[:5]}"))

    for p, c in counts.items():
        base = content_by_path[p]
        if not base.repeatable and c != 1:
            findings.append(
                VerifyFinding(
                    "ERROR",
                    f"Non-repeatable item appears {c} times (must be exactly 1): {p}",
                )
            )
        if base.repeatable:
            if c > 1 + base.max_extra_uses:
                findings.append(
                    VerifyFinding(
                        "ERROR",
                        f"Repeatable item exceeds max_extra_uses. Appears {c} times, limit is {1 + base.max_extra_uses}: {p}",
                    )
                )

    # 5) Bumper exhaust-before-repeat (per bumper pool).
    # For M bumpers in a pool, no bumper should repeat within the next M-1 uses *of that pool*.
    for pool_name, pool in cfg.bumpers.pools.items():
        pool_paths = [it.path for it in pool.items]
        if len(pool_paths) <= 1:
            continue
        pool_set = set(pool_paths)
        last_seen: dict[str, int] = {}
        seen_count = 0
        for it in items:
            p = it["path"]
            if p not in pool_set:
                continue
            if p in last_seen:
                gap = seen_count - last_seen[p]
                if gap < len(pool_set):
                    findings.append(
                        VerifyFinding(
                            "ERROR",
                            f"Bumper repeats before exhaustion in pool {pool_name!r}. {p} repeated after {gap} uses; need >= {len(pool_set)}",
                        )
                    )
                    break
            last_seen[p] = seen_count
            seen_count += 1

    # 6) Block duration checks (content only).
    cap_s = cfg.solver.block_s
    ceiling_s = cap_s + cfg.solver.allow_short_overflow_s
    # Split into blocks by bumper/content runs.
    blocks: list[list[str]] = []
    for is_b, ln, start in runs:
        if is_b:
            continue
        block = [items[i]["path"] for i in range(start, start + ln)]
        blocks.append(block)

    if not blocks:
        findings.append(VerifyFinding("ERROR", "No content blocks found (playlist had bumpers only?)"))
    else:
        for bi, block in enumerate(blocks):
            dur_s = sum(duration_by_path_s.get(p, 0) for p in block)
            if not block:
                findings.append(VerifyFinding("ERROR", f"Empty content block at block index {bi}"))
                continue

            # Long-form rule: if any item is >= cap, the block must contain exactly one item.
            if cfg.solver.longform_consumes_block:
                long_items = [p for p in block if duration_by_path_s.get(p, 0) >= cap_s]
                if long_items:
                    if len(block) != 1:
                        findings.append(
                            VerifyFinding(
                                "ERROR",
                                f"Block {bi} contains long-form content but also other items. Long items: {long_items[:3]}",
                            )
                        )
                        continue
                    # Long-form blocks are allowed to exceed the block size.
                    # The whole point is "this item consumes one block (overflow allowed)".
                    continue

            if dur_s > ceiling_s:
                findings.append(
                    VerifyFinding(
                        "ERROR",
                        f"Block {bi} exceeds target capacity: {dur_s/60:.1f} min > {ceiling_s/60:.1f} min",
                    )
                )

    # 7) Sequential pools in order (SxxExx parsing).
    for pool_name, pool_cfg in cfg.pools.items():
        if not pool_cfg.sequential:
            continue
        # Gather occurrences in playlist order.
        eps = []
        for it in items:
            p = it["path"]
            if p in bumper_set:
                continue
            base = content_by_path.get(p)
            if base and base.pool == pool_name:
                eid = parse_sxxexx(p)
                if eid is None:
                    findings.append(VerifyFinding("ERROR", f"Sequential pool item missing SxxExx: {p}"))
                    continue
                eps.append((eid.season, eid.episode, p))

        # Check nondecreasing by (season, episode).
        for (s1, e1, p1), (s2, e2, p2) in zip(eps, eps[1:]):
            if (s2, e2) < (s1, e1):
                findings.append(
                    VerifyFinding(
                        "ERROR",
                        f"Sequential pool {pool_name!r} is out of order: {p1} then {p2}",
                    )
                )
                break

    return findings
