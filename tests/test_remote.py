"""Integration test: two real daemons + a relay, cross-node ask/reply.

This is the full remote path — exactly what happens between two different
machines, but run locally against one relay. It exercises: relay routing,
the consent handshake, end-to-end encryption (PyNaCl), and ask/reply
correlation across nodes.

Run from the repo root:

    PYTHONPATH=. python tests/test_remote.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile

RELAY_PORT = 8790
RELAY_URL = f"ws://127.0.0.1:{RELAY_PORT}"


async def send(w: asyncio.StreamWriter, obj: dict) -> None:
    w.write((json.dumps(obj) + "\n").encode())
    await w.drain()


async def recv_event(r: asyncio.StreamReader, want: str, timeout: float = 8.0) -> dict:
    while True:
        line = await asyncio.wait_for(r.readline(), timeout)
        if not line:
            raise EOFError("daemon closed connection")
        m = json.loads(line)
        if m.get("event") == want:
            return m


def start_daemon(home: str) -> subprocess.Popen:
    # AUTO_ACCEPT so the receiving node consents without an interactive TUI.
    env = dict(os.environ, AGENTSYNC_HOME=home, AGENTSYNC_RELAY=RELAY_URL, AGENTSYNC_AUTO_ACCEPT="1")
    return subprocess.Popen(
        [sys.executable, "-m", "agentsync.daemon"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


async def wait_socket(path: str, timeout: float = 8.0):
    for _ in range(int(timeout / 0.1)):
        if os.path.exists(path):
            try:
                return await asyncio.open_unix_connection(path)
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await asyncio.sleep(0.1)
    raise RuntimeError(f"socket never came up: {path}")


async def node_id_of(path: str) -> str:
    r, w = await asyncio.open_unix_connection(path)
    await send(w, {"cmd": "hello", "label": "probe", "role": "control"})
    welcome = await recv_event(r, "welcome")
    w.close()
    return welcome["node_id"]


async def run() -> None:
    relay = subprocess.Popen(
        [sys.executable, "-m", "agentsync.relay.server", "--port", str(RELAY_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    home_a = tempfile.mkdtemp(prefix="as-A-")
    home_b = tempfile.mkdtemp(prefix="as-B-")
    da, db = start_daemon(home_a), start_daemon(home_b)
    procs = [relay, da, db]
    try:
        sock_a = os.path.join(home_a, "daemon.sock")
        sock_b = os.path.join(home_b, "daemon.sock")
        ra, wa = await wait_socket(sock_a)   # answerer session on node A
        rb, wb = await wait_socket(sock_b)   # asker session on node B
        node_a = await node_id_of(sock_a)
        node_b = await node_id_of(sock_b)
        print(f"node A = {node_a}   node B = {node_b}")

        await send(wa, {"cmd": "hello", "label": "answerer-A", "role": "session"})
        await recv_event(ra, "welcome")
        await send(wb, {"cmd": "hello", "label": "asker-B", "role": "session"})
        await recv_event(rb, "welcome")

        # B connects to A — retry until both daemons have linked to the relay.
        res = {}
        for _ in range(20):
            await send(wb, {"cmd": "connect", "target": node_a})
            res = await recv_event(rb, "connect_result", timeout=8)
            if res.get("ok"):
                break
            await asyncio.sleep(0.4)
        assert res.get("ok"), f"B could not connect to A: {res}"
        print("B connected to A — consent accepted, E2E box established")

        # B asks A a question over the relay.
        await send(wb, {"cmd": "ask", "target": node_a, "prompt": "capital of France?", "request_id": "r1"})
        ask = await recv_event(ra, "ask")
        assert ask["request_id"] == "r1" and "France" in ask["prompt"], ask
        print(f"A received remote ask from {ask['from_label']!r}: {ask['prompt']!r}")

        await send(wa, {"cmd": "reply", "request_id": "r1", "body": "Paris", "ok": True})
        rep = await recv_event(rb, "reply")
        assert rep["request_id"] == "r1" and rep["body"] == "Paris" and rep["ok"] is True, rep
        print(f"B received the reply (decrypted off the relay): {rep['body']!r}")

        print("\nSMOKE OK — remote cross-node ask/reply works (relay + E2E + consent)")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    asyncio.run(run())
