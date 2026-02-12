from __future__ import annotations

import math


class DurationError(Exception):
    pass


def parse_hhmmss_to_seconds(value: str) -> int:
    """
    Parse ErsatzTV's stored duration format into seconds.

    Expected formats:
    - "HH:MM:SS"
    - "H:MM:SS"

    Returns an integer number of seconds.
    Raises DurationError on invalid input.
    """
    if value is None:
        raise DurationError("duration is null")
    if not isinstance(value, str):
        raise DurationError(f"duration must be a string, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise DurationError("duration is empty")
    parts = s.split(":")
    if len(parts) != 3:
        raise DurationError(f"duration must look like HH:MM:SS, got {value!r}")
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        # ErsatzTV sometimes stores fractional seconds (e.g. "0:07:23.456").
        # We floor to whole seconds.
        ss_f = float(parts[2])
        ss = int(math.floor(ss_f))
    except ValueError as e:
        raise DurationError(f"duration contains non-integer component: {value!r}") from e
    if hh < 0 or mm < 0 or ss < 0 or mm >= 60 or ss >= 60:
        raise DurationError(f"duration out of range: {value!r}")
    return hh * 3600 + mm * 60 + ss


def seconds_to_minutes_float(seconds: int, *, precision: int = 3) -> float:
    if seconds < 0:
        raise DurationError("seconds must be non-negative")
    return round(seconds / 60.0, precision)
