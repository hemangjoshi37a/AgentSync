"""Smoke test: two local Claude sessions bridged through the daemon (no relay).

Verifies the same-PC path: two clients register, one asks the other, the other
replies, and the answer routes back — all over the Unix socket. Run from repo
root:

    PYTHONPATH=. python tests/smoke_local.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile


async def send(w: asyncio.StreamWriter, obj: dict) -> None:
    w.write((json.dumps(obj) + "\n").encode())
    await w.drain()


async def recv_event(r: asyncio.StreamReader, want: str, timeout: float = 5.0) -> dict:
    while True:
        line = await asyncio.wait_for(r.readline(), timeout)
        if not line:
            raise EOFError("daemon closed connection")
        m = json.loads(line)
        if m.get("event") == want:
            return m


async def run() -> None:
    home = tempfile.mkdtemp(prefix="agentsync-test-")
    env = dict(os.environ, AGENTSYNC_HOME=home, AGENTSYNC_NO_RELAY="1")
    proc = subprocess.Popen([sys.executable, "-m", "agentsync.daemon"], env=env)
    sock = os.path.join(home, "daemon.sock")
    try:
        r1 = w1 = None
        for _ in range(50):
            if os.path.exists(sock):
                try:
                    r1, w1 = await asyncio.open_unix_connection(sock)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
            await asyncio.sleep(0.1)
        assert r1 is not None and w1 is not None, "daemon socket never came up"
        r2, w2 = await asyncio.open_unix_connection(sock)

        await send(w1, {"cmd": "hello", "label": "session-one"})
        info1 = await recv_event(r1, "welcome")
        s1 = info1["session_id"]
        await send(w2, {"cmd": "hello", "label": "session-two"})
        info2 = await recv_event(r2, "welcome")
        s2 = info2["session_id"]
        print(f"two sessions on node {info1['node_id']}: {s1}, {s2}")

        # s1 asks s2
        await send(w1, {"cmd": "ask", "target": s2, "prompt": "what is 2+2?", "request_id": "q1"})
        ask = await recv_event(r2, "ask")
        assert ask["prompt"] == "what is 2+2?" and ask["request_id"] == "q1", ask
        print(f"{s2} received ask from {ask['from_label']!r}: {ask['prompt']!r}")

        await send(w2, {"cmd": "reply", "request_id": "q1", "body": "4", "ok": True})
        rep = await recv_event(r1, "reply")
        assert rep["request_id"] == "q1" and rep["body"] == "4" and rep["ok"] is True, rep
        print(f"{s1} received reply: {rep['body']!r}")

        # fire-and-forget the other direction
        await send(w2, {"cmd": "send", "target": s1, "body": "thanks!"})
        m = await recv_event(r1, "message")
        assert m["body"] == "thanks!", m
        print(f"{s1} received message: {m['body']!r}")

        print("\nSMOKE OK — local session-to-session bridge works")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(run())
