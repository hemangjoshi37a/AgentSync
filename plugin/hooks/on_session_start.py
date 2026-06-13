#!/usr/bin/env python3
"""AgentSync SessionStart hook.

Injects awareness context into a starting/resuming Claude Code session so the
model knows it can reach other Claude sessions through the AgentSync daemon.

Behavior (all best-effort):
  1. Read this node's id + label from ~/.agentsync/config.toml
     (honoring the AGENTSYNC_HOME env var), parsed with tomllib.
  2. Briefly open the daemon's Unix socket, say `hello`, ask for `peers`, and
     list the peers that can be reached right now.
  3. Print a SessionStart hookSpecificOutput block with additionalContext and
     exit 0.

FAIL-SAFE: everything is wrapped in try/except. On ANY error (missing config,
daemon down, timeout, malformed data) the hook prints nothing and exits 0.
A hook must never block, crash, or otherwise disrupt the session.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tomllib
from pathlib import Path

# How long we are willing to wait on the daemon socket, in seconds.
SOCKET_TIMEOUT = 1.5


def _agentsync_home() -> Path:
    """Resolve the AgentSync config/state directory."""
    home = os.environ.get("AGENTSYNC_HOME")
    if home:
        return Path(home)
    return Path.home() / ".agentsync"


def _socket_path(base: Path) -> Path:
    """Resolve the daemon Unix socket path."""
    override = os.environ.get("AGENTSYNC_SOCKET")
    if override:
        return Path(override)
    return base / "daemon.sock"


def _load_identity(base: Path) -> tuple[str, str]:
    """Return (node_id, label) from config.toml. Raises on any problem."""
    config_file = base / "config.toml"
    data = tomllib.loads(config_file.read_text())
    node_id = str(data["node_id"])
    label = str(data.get("label", "")) or node_id
    return node_id, label


def _query_peers(sock_path: Path) -> list[str]:
    """Open the daemon socket, do a hello/peers handshake, and return a list of
    human-readable connectable-peer descriptions. Best-effort: returns [] on
    any failure and never raises out of the read loop."""
    descriptions: list[str] = []
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(SOCKET_TIMEOUT)
    try:
        sock.connect(str(sock_path))
        # First message MUST be a hello (per PROTOCOL.md).
        sock.sendall(
            json.dumps({"cmd": "hello", "label": "hook", "role": "control"}).encode()
            + b"\n"
        )
        sock.sendall(json.dumps({"cmd": "peers"}).encode() + b"\n")

        # Read newline-delimited JSON until we see a `peers` event (or time out).
        buf = b""
        peers_event = None
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode())
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(msg, dict) and msg.get("event") == "peers":
                    peers_event = msg
                    break
            if peers_event is not None:
                break
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if not isinstance(peers_event, dict):
        return descriptions

    # Local peers: addressed by session id.
    for p in peers_event.get("local", []) or []:
        if not isinstance(p, dict):
            continue
        sid = p.get("session_id")
        if not sid:
            continue
        lbl = p.get("label")
        descriptions.append(f"{sid} ({lbl})" if lbl else str(sid))

    # Remote peers: addressed by node id; skip ones that are paused.
    for p in peers_event.get("remote", []) or []:
        if not isinstance(p, dict):
            continue
        if p.get("paused"):
            continue
        nid = p.get("node_id")
        if not nid:
            continue
        lbl = p.get("label")
        descriptions.append(f"{nid} ({lbl})" if lbl else str(nid))

    return descriptions


def main() -> None:
    base = _agentsync_home()

    # Zero-setup: ensure the local daemon is running, starting it from the
    # bundled runtime if needed — so installing the plugin is all the user does.
    try:
        runtime = Path(__file__).resolve().parent.parent / "runtime"
        if str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        import bootstrap  # bundled in plugin/runtime/

        bootstrap.ensure_daemon(runtime)
    except Exception:
        pass

    node_id, label = _load_identity(base)

    # Peer discovery is best-effort and must not abort context injection.
    try:
        peers = _query_peers(_socket_path(base))
    except Exception:
        peers = []

    connectable = ", ".join(peers) if peers else "none"
    context = (
        f"AgentSync node {node_id} ({label}) is online. "
        "You can reach other Claude sessions with the agentsync_* tools "
        "(agentsync_peers, agentsync_connect, agentsync_ask, agentsync_inbox, "
        "agentsync_respond). "
        f"Connectable now: {connectable}."
    )

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Never block or break the session.
        sys.exit(0)
