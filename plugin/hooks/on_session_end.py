#!/usr/bin/env python3
"""AgentSync SessionEnd hook.

A stdlib-only, fail-safe no-op. It optionally appends a best-effort line to a
log file under the AgentSync home directory, then always exits 0. It never
raises and never disrupts session teardown.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _agentsync_home() -> Path:
    home = os.environ.get("AGENTSYNC_HOME")
    if home:
        return Path(home)
    return Path.home() / ".agentsync"


def main() -> None:
    # Read (and ignore) the hook payload on stdin; never fail if it is empty
    # or malformed.
    session_id = ""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
            if isinstance(payload, dict):
                session_id = str(payload.get("session_id", ""))
    except Exception:
        session_id = ""

    # Best-effort log; only if the AgentSync home already exists so we never
    # create state for a node that isn't configured.
    try:
        base = _agentsync_home()
        if base.is_dir():
            line = f"{int(time.time())}\tsession_end\t{session_id}\n"
            with open(base / "hook.log", "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        # Always succeed.
        sys.exit(0)
