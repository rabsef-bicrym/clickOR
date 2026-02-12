from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class EnvError(Exception):
    pass


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def load_dotenv(path: str | Path, *, override: bool = False) -> None:
    """
    Load a .env file into process environment.

    Rules:
    - Lines starting with # are ignored
    - Empty lines are ignored
    - KEY=VALUE pairs only
    - Values may be quoted with single or double quotes
    - We do not expand ${VARS} here (keep it predictable)
    - By default we do not override already-set environment variables
    """
    path = Path(path)
    if not path.exists():
        return

    for idx, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvError(f"{path}:{idx}: expected KEY=VALUE, got {raw!r}")
        k, v = line.split("=", 1)
        k = k.strip()
        v = _strip_quotes(v.strip())
        if not k:
            raise EnvError(f"{path}:{idx}: empty KEY in {raw!r}")
        if (k in os.environ) and not override:
            continue
        os.environ[k] = v


@dataclass(frozen=True)
class ClickorEnv:
    """
    Environment-backed operational defaults (optional).

    These are only defaults. CLI flags always win.
    """

    ssh_prefix: Optional[str]
    db_path: Optional[str]
    ssh_sudo: bool
    base_url: Optional[str]
    reset_after_apply: bool


def read_env() -> ClickorEnv:
    """
    Read CLICKOR_* keys from the current process environment.
    """
    ssh_prefix = os.environ.get("CLICKOR_SSH") or None
    db_path = os.environ.get("CLICKOR_DB_PATH") or None
    base_url = os.environ.get("CLICKOR_BASE_URL") or None

    def b(name: str, default: bool) -> bool:
        v = os.environ.get(name)
        if v is None:
            return default
        return v.strip().lower() in ("1", "true", "yes", "y", "on")

    ssh_sudo = b("CLICKOR_SSH_SUDO", True if ssh_prefix else False)
    reset_after_apply = b("CLICKOR_RESET_AFTER_APPLY", True)

    return ClickorEnv(
        ssh_prefix=ssh_prefix,
        db_path=db_path,
        ssh_sudo=ssh_sudo,
        base_url=base_url,
        reset_after_apply=reset_after_apply,
    )
