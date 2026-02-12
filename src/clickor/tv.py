from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


SXXEXX_RE = re.compile(r"(?i)\bS(?P<s>\d{1,2})E(?P<e>\d{1,2})\b")


@dataclass(frozen=True)
class EpisodeId:
    season: int
    episode: int


def parse_sxxexx(path: str) -> Optional[EpisodeId]:
    """
    Extract (season, episode) from any substring like:
      S01E02
      s1e2

    Returns None if not found.

    Note:
    - We intentionally do not attempt to parse more exotic naming patterns.
      If you need those, add them explicitly rather than guessing.
    """
    m = SXXEXX_RE.search(path)
    if not m:
        return None
    return EpisodeId(season=int(m.group("s")), episode=int(m.group("e")))
