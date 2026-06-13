#!/usr/bin/env python3
"""AgentSync status line for the Claude Code status bar.

Prints a one-line summary of this machine's AgentSync identity and live
connection state — the node id (the address other machines use to reach this
one), how many local Claude sessions are on the AgentSync network, and which
remote machines are currently connected.

Pure standard library, so it runs under whatever ``python3`` Claude Code uses
and can be invoked directly as a status-line command:

    python3 ~/.agentsync/statusline.py     # stable copy maintained by the plugin
    agentsync statusline                    # same thing, via the CLI

Claude Code passes session JSON on stdin (ignored here). Never errors loudly —
worst case it prints a minimal label so the status bar still renders.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def _home() -> Path:
    h = os.environ.get("AGENTSYNC_HOME")
    return Path(h) if h else Path.home() / ".agentsync"


def _identity() -> tuple[str, str]:
    """(node_id, label) from config.toml — best effort."""
    try:
        import tomllib

        data = tomllib.loads((_home() / "config.toml").read_text())
        return str(data.get("node_id", "AS-?")), str(data.get("label", ""))
    except Exception:
        return "AS-?", ""


def _peers(timeout: float = 0.8) -> dict | None:
    """Query the daemon for a peer snapshot. Returns None if the daemon is down."""
    sock_path = os.environ.get("AGENTSYNC_SOCKET") or str(_home() / "daemon.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(b'{"cmd":"hello","label":"statusline","role":"control"}\n')
        s.sendall(b'{"cmd":"peers"}\n')
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                return None
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("event") == "peers":
                    return msg
    except OSError:
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def render() -> str:
    node_id, _ = _identity()
    snap = _peers()
    if snap is None:
        return f"🔗 {node_id} · ⚠ daemon off"

    local = [p for p in snap.get("local", []) if isinstance(p, dict)]
    remote = [
        p for p in snap.get("remote", [])
        if isinstance(p, dict) and not p.get("paused")
    ]

    parts = [f"🔗 {node_id}"]
    if local:
        parts.append(f"{len(local)} local")
    if remote:
        names = [str(p.get("label") or p.get("node_id")) for p in remote]
        if len(names) <= 2:
            parts.append("↔ " + ", ".join(names))
        else:
            parts.append("↔ " + ", ".join(names[:2]) + f" +{len(names) - 2}")
    else:
        parts.append("no remote peers")
    return " · ".join(parts)


def main() -> None:
    try:
        sys.stdin.read()  # consume Claude's session JSON (unused)
    except Exception:
        pass
    try:
        print(render())
    except Exception:
        print("🔗 AgentSync")


if __name__ == "__main__":
    main()
