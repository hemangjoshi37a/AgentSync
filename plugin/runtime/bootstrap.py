"""Zero-setup daemon bootstrap, shared by the SessionStart hook and MCP server.

Ensures a local AgentSync daemon is running, starting one (detached, from the
bundled runtime in this directory) if it isn't. Pure standard library so it
works under whatever ``python3`` Claude Code uses — the daemon runs in
local-only mode with no third-party dependencies. Never raises.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _home() -> Path:
    h = os.environ.get("AGENTSYNC_HOME")
    return Path(h) if h else Path.home() / ".agentsync"


def socket_path() -> Path:
    override = os.environ.get("AGENTSYNC_SOCKET")
    return Path(override) if override else _home() / "daemon.sock"


def _is_live(path: Path) -> bool:
    if not path.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def ensure_daemon(runtime_dir: str | os.PathLike, timeout: float = 6.0) -> bool:
    """Return True if a daemon is running, starting one from runtime_dir if needed.

    runtime_dir must contain the bundled ``agentsync`` package. Fully best-effort:
    returns False (never raises) if it cannot start or reach the daemon.
    """
    try:
        sp = socket_path()
        if _is_live(sp):
            return True
        home = _home()
        home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(runtime_dir) + (os.pathsep + existing if existing else "")
        logf = open(home / "daemon.log", "ab")
        subprocess.Popen(
            [sys.executable, "-m", "agentsync.daemon"],
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach: daemon outlives this session
            env=env,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _is_live(sp):
                return True
            time.sleep(0.15)
        return False
    except Exception:
        return False
