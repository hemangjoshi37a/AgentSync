"""AgentSync command-line interface — the `agentsync` entrypoint.

Subcommands:
    up        ensure the daemon is running, then open the TUI console
    daemon    run the daemon in the foreground (service mode)
    id        print this node's AgentSync id, label, and relay
    peers     list connectable peers (local + remote)
    connect   ask the daemon to connect to a peer by id
    status    show daemon status + peers
    stop      stop the running daemon

The daemon runs as a detached background process so that closing the TUI does
not disconnect you (AnyDesk-style: you stay reachable until you `stop`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket as _socket
import subprocess
import sys
import time

from . import config as C


# --------------------------------------------------------------------------- #
# daemon liveness / lifecycle
# --------------------------------------------------------------------------- #
def _socket_live() -> bool:
    if not C.SOCKET_PATH.exists():
        return False
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.settimeout(1.0)
        s.connect(str(C.SOCKET_PATH))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_daemon(timeout: float = 8.0) -> bool:
    """Start the daemon as a detached background process if not already up."""
    if _socket_live():
        return True
    C.ensure_dir()
    logf = open(C.LOG_FILE, "ab")
    subprocess.Popen(
        [sys.executable, "-m", "agentsync.daemon"],
        stdout=logf,
        stderr=logf,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _socket_live():
            return True
        time.sleep(0.15)
    return False


# --------------------------------------------------------------------------- #
# one-shot socket query helper (for peers / connect / status)
# --------------------------------------------------------------------------- #
async def _query(commands: list[dict], collect_event: str, timeout: float = 5.0) -> dict | None:
    reader, writer = await asyncio.open_unix_connection(str(C.SOCKET_PATH))
    try:
        def _w(obj: dict) -> None:
            writer.write((json.dumps(obj) + "\n").encode())

        _w({"cmd": "hello", "label": "cli", "role": "control"})
        for c in commands:
            _w(c)
        await writer.drain()
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout)
            if not line:
                return None
            event = json.loads(line)
            if event.get("event") == collect_event:
                return event
    finally:
        writer.close()


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_id(_args) -> int:
    cfg, _ = C.load_or_create()
    print(f"AgentSync node id : {cfg.node_id}")
    print(f"label             : {cfg.label}")
    print(f"relay             : {cfg.relay_url}")
    print(f"socket            : {C.SOCKET_PATH}")
    print(f"daemon running    : {'yes' if _socket_live() else 'no'}")
    return 0


def cmd_up(_args) -> int:
    cfg, _ = C.load_or_create()
    print(f"AgentSync node {cfg.node_id} ({cfg.label})")
    if not ensure_daemon():
        print(f"failed to start daemon; see {C.LOG_FILE}", file=sys.stderr)
        return 1
    print("daemon running. opening console — closing it keeps the daemon alive (use `agentsync stop` to disconnect).")
    from .tui import run_tui

    run_tui()
    return 0


def cmd_daemon(_args) -> int:
    from .daemon import Daemon

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg, priv = C.load_or_create()
    try:
        asyncio.run(Daemon(cfg, priv).start())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_peers(_args) -> int:
    if not _socket_live():
        print("daemon not running — start it with:  agentsync up")
        return 1
    event = asyncio.run(_query([{"cmd": "peers"}], "peers"))
    if event is None:
        print("no response from daemon")
        return 1
    local = event.get("local", [])
    remote = event.get("remote", [])
    print(f"node {event.get('node_id')}\n")
    print(f"local sessions ({len(local)}):")
    for s in local:
        print(f"  - {s.get('session_id',''):6}  {s.get('label','')}")
    print(f"\nremote peers ({len(remote)}):")
    for p in remote:
        flag = "  (paused)" if p.get("paused") else ""
        print(f"  - {p.get('node_id',''):14} {p.get('label','')}{flag}")
    if not local and not remote:
        print("  (none yet — connect with: agentsync connect <peer-id>)")
    return 0


def cmd_connect(args) -> int:
    if not _socket_live():
        print("daemon not running — start it with:  agentsync up")
        return 1
    event = asyncio.run(_query([{"cmd": "connect", "target": args.peer}], "connect_result", timeout=45))
    if event is None:
        print("no response (timed out waiting for the peer to consent)")
        return 1
    if event.get("ok"):
        print(f"connected to {event.get('peer')}")
        return 0
    print(f"connect failed: {event.get('reason')}")
    return 1


def cmd_stop(_args) -> int:
    if not C.PIDFILE.exists():
        print("no daemon pidfile — daemon not running?")
        return 1
    try:
        pid = int(C.PIDFILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"sent SIGTERM to daemon (pid {pid})")
        return 0
    except (ProcessLookupError, ValueError, OSError) as exc:
        print(f"could not stop daemon: {exc}")
        return 1


def cmd_set_relay(args) -> int:
    cfg, _ = C.load_or_create()
    cfg.relay_url = args.url
    C.save(cfg)
    print(f"relay set to {cfg.relay_url}")
    if os.environ.get("AGENTSYNC_RELAY"):
        print("note: AGENTSYNC_RELAY is set in your environment and overrides this at runtime.")
    if _socket_live():
        print("restart the daemon to apply:  agentsync stop && agentsync up")
    return 0


def _set_trust(node: str | None, all_: bool, remove: bool) -> int:
    if not all_ and not node:
        print("specify a peer node id, or use --all")
        return 1
    verb = "untrusted" if remove else "trusted"
    if _socket_live():
        cmd: dict = {"cmd": "untrust" if remove else "trust"}
        if all_:
            cmd["all"] = True
        else:
            cmd["node"] = node
        ev = asyncio.run(_query([cmd], "untrusted" if remove else "trusted"))
        if ev is None:
            print("no response from daemon")
            return 1
        if all_:
            print("trust-all-remote " + ("disabled" if remove else "ENABLED — all peers auto-accept"))
        else:
            print(f"{verb} {node}")
            tn = ev.get("trusted_nodes")
            if tn is not None:
                print("trusted peers:", ", ".join(tn) if tn else "(none)")
        return 0
    # daemon not running — persist directly to config
    cfg, _ = C.load_or_create()
    pol = cfg.policy
    if all_:
        pol.trust_all_remote = not remove
    elif remove:
        if node in pol.trusted_nodes:
            pol.trusted_nodes.remove(node)
    elif node and node not in pol.trusted_nodes:
        pol.trusted_nodes.append(node)
    C.save(cfg)
    print(f"{verb} {'ALL remote peers' if all_ else node} (persisted; daemon not running)")
    return 0


def cmd_trust(args) -> int:
    return _set_trust(args.node, args.all, remove=False)


def cmd_untrust(args) -> int:
    return _set_trust(args.node, args.all, remove=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agentsync", description="AnyDesk for Claude Code sessions.")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("up", help="ensure the daemon is running, then open the TUI console").set_defaults(func=cmd_up)
    sub.add_parser("daemon", help="run the daemon in the foreground (service mode)").set_defaults(func=cmd_daemon)
    sub.add_parser("id", help="print this node's AgentSync id, label, and relay").set_defaults(func=cmd_id)
    sub.add_parser("peers", help="list connectable peers (local + remote)").set_defaults(func=cmd_peers)
    sub.add_parser("status", help="show daemon status + peers").set_defaults(func=cmd_peers)
    sub.add_parser("stop", help="stop the running daemon").set_defaults(func=cmd_stop)

    pc = sub.add_parser("connect", help="connect to a peer by id")
    pc.add_argument("peer", help="peer node id (AS-XXXX-XXXX) or local session id (sN)")
    pc.set_defaults(func=cmd_connect)

    pr = sub.add_parser("set-relay", help="set the rendezvous relay URL (persisted to config)")
    pr.add_argument("url", help="relay websocket URL, e.g. wss://relay.example:8787 or ws://127.0.0.1:8787")
    pr.set_defaults(func=cmd_set_relay)

    pt = sub.add_parser("trust", help="permanently trust a peer so its connections auto-accept")
    pt.add_argument("node", nargs="?", help="peer node id to trust (omit when using --all)")
    pt.add_argument("--all", action="store_true", help="auto-accept ALL remote peers (use with caution)")
    pt.set_defaults(func=cmd_trust)

    pu = sub.add_parser("untrust", help="stop trusting a peer (or --all)")
    pu.add_argument("node", nargs="?", help="peer node id to untrust (omit when using --all)")
    pu.add_argument("--all", action="store_true", help="disable trust-all-remote")
    pu.set_defaults(func=cmd_untrust)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
