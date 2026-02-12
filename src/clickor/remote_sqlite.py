from __future__ import annotations

import shlex
import subprocess
import os
from dataclasses import dataclass
from typing import Optional


class RemoteSqliteError(Exception):
    pass


@dataclass(frozen=True)
class Ssh:
    args: list[str]  # full ssh command argv, e.g. ["ssh","-i","...","user@host"]


def parse_ssh_prefix(prefix: str) -> Ssh:
    """
    Parse an ssh prefix string into argv safely.

    Example input:
      "ssh -i ~/.ssh/your_key user@host"
    """
    args = [os.path.expanduser(a) for a in shlex.split(prefix)]
    if not args or args[0] != "ssh":
        raise RemoteSqliteError("--ssh must start with 'ssh ...'")
    if len(args) < 2:
        raise RemoteSqliteError("--ssh must include a host, like 'ssh user@host'")
    return Ssh(args=args)


def run_sqlite(
    *,
    sql: str,
    db_path: str,
    ssh: Optional[Ssh],
    sudo: bool,
) -> str:
    """
    Execute SQL by sending it to sqlite3 over stdin.

    This avoids:
    - shell quoting issues
    - long command lines
    - SCP temp files

    When using --ssh, this runs:
      ssh ... [sudo] sqlite3 <db_path>
    """
    if ssh is None:
        cmd = ["sqlite3", db_path]
    else:
        remote = ["sqlite3", db_path]
        if sudo:
            remote = ["sudo"] + remote
        cmd = ssh.args + remote

    r = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        raise RemoteSqliteError(f"sqlite3 failed: {stderr}")
    return (r.stdout or "").strip()
