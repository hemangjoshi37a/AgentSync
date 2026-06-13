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


def render(my_cwd: str | None = None) -> str:
    if os.environ.get("NO_COLOR"):
        title = key = val = reset = ""
    else:
        title = "\033[1;36m"  # bold cyan
        key = "\033[2m"       # dim
        val = "\033[1m"       # bold
        reset = "\033[0m"

    node_id, _ = _identity()
    snap = _peers()
    if snap is None:
        return f"{title}🔗 AgentSync{reset} {key}: Node ={reset} {val}{node_id}{reset} {key}(daemon off){reset}"

    local = [p for p in snap.get("local", []) if isinstance(p, dict)]
    remote = [p for p in snap.get("remote", []) if isinstance(p, dict) and not p.get("paused")]

    # Identify *this* session: match the session whose cwd equals ours; if there
    # is only one local session, that's us.
    me = None
    if my_cwd:
        me = next((p for p in local if str(p.get("cwd", "")) == my_cwd), None)
    if me is None and len(local) == 1:
        me = local[0]
    sid = str(me.get("session_id", "?")) if me else "?"
    label = str(me.get("label", "")) if me else ""

    def _fmt(p: dict) -> str:
        s = str(p.get("session_id", ""))
        lbl = str(p.get("label", ""))
        return f"{s}·{lbl}" if lbl else s

    # Other local sessions you can address (everything except this one).
    others = [p for p in local if p is not me]
    if others:
        olist = ", ".join(_fmt(p) for p in others[:4])
        if len(others) > 4:
            olist += f" +{len(others) - 4}"
    else:
        olist = "none"

    # Remote machines currently connected.
    if remote:
        rlist = ", ".join(str(p.get("label") or p.get("node_id")) for p in remote[:3])
        if len(remote) > 3:
            rlist += f" +{len(remote) - 3}"
    else:
        rlist = "none"

    return (
        f"{title}🔗 AgentSync{reset} {key}:{reset} "
        f"{key}Node ={reset} {val}{node_id}{reset}{key},{reset} "
        f"{key}Session ={reset} {val}{sid}{reset}{key},{reset} "
        f"{key}Label ={reset} {val}{label or '-'}{reset}{key},{reset} "
        f"{key}Local ={reset} {val}{olist}{reset}{key},{reset} "
        f"{key}Peers ={reset} {val}{rlist}{reset}"
    )


def main() -> None:
    # Claude Code passes session JSON on stdin; use its cwd to identify which
    # local session is "this" one (the MCP server reports its cwd on register).
    my_cwd = None
    try:
        raw = sys.stdin.read()
        if raw.strip():
            info = json.loads(raw)
            ws = info.get("workspace") or {}
            # project_dir is the launch dir = the MCP server's os.getcwd(), and
            # stays stable even if the session later cd's elsewhere.
            cwd = ws.get("project_dir") or ws.get("current_dir") or info.get("cwd")
            if cwd:
                my_cwd = str(cwd)
    except Exception:
        my_cwd = None
    try:
        print(render(my_cwd))
    except Exception:
        print("🔗 AgentSync")


if __name__ == "__main__":
    main()
