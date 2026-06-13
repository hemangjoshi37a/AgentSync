"""Stress / battle test for the AgentSync daemon routing layer.

Hammers the local Unix-socket hub with concurrent sessions, a full ask/reply
mesh, rapid-fire asks, a large payload, the unknown-target error path, and
disconnect propagation — looking for routing / correlation / lifecycle bugs.

    PYTHONPATH=. python tests/stress.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL  {msg}")


class Client:
    def __init__(self, reader, writer, sid, label):
        self.r, self.w, self.sid, self.label = reader, writer, sid, label
        self.replies: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.asks_seen = 0
        self._task = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while True:
                line = await self.r.readline()
                if not line:
                    break
                m = json.loads(line)
                ev = m.get("event")
                if ev == "ask":
                    self.asks_seen += 1
                    await self.send({"cmd": "reply", "request_id": m["request_id"],
                                     "body": "echo:" + m["prompt"], "ok": True})
                elif ev == "reply":
                    fut = self.replies.pop(m["request_id"], None)
                    if fut and not fut.done():
                        fut.set_result(m)
                else:
                    await self.events.put(m)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass

    async def send(self, obj):
        self.w.write((json.dumps(obj) + "\n").encode())
        await self.w.drain()

    async def ask(self, target, prompt, rid, timeout=15):
        fut = asyncio.get_event_loop().create_future()
        self.replies[rid] = fut
        await self.send({"cmd": "ask", "target": target, "prompt": prompt, "request_id": rid})
        return await asyncio.wait_for(fut, timeout)

    async def close(self):
        self.w.close()


async def connect(sock, label) -> Client:
    r, w = await asyncio.open_unix_connection(sock, limit=16 * 1024 * 1024)
    w.write((json.dumps({"cmd": "hello", "label": label, "role": "session"}) + "\n").encode())
    await w.drain()
    while True:
        line = await r.readline()
        m = json.loads(line)
        if m.get("event") == "welcome":
            return Client(r, w, m["session_id"], label)


async def run() -> None:
    home = tempfile.mkdtemp(prefix="as-stress-")
    env = dict(os.environ, AGENTSYNC_HOME=home, AGENTSYNC_NO_RELAY="1")
    proc = subprocess.Popen([sys.executable, "-m", "agentsync.daemon"], env=env)
    sock = os.path.join(home, "daemon.sock")
    try:
        for _ in range(50):
            if os.path.exists(sock):
                try:
                    probe = await asyncio.open_unix_connection(sock)
                    probe[1].close()
                    break
                except OSError:
                    pass
            await asyncio.sleep(0.1)

        N = 5
        clients = [await connect(sock, f"node{i}") for i in range(N)]
        ids = [c.sid for c in clients]
        print(f"[1] connected {N} sessions: {ids}")

        # --- full mesh: every client asks every other concurrently ---
        async def one(i, j):
            rid = f"m-{i}-{j}"
            rep = await clients[i].ask(ids[j], f"hi from {i} to {j}", rid)
            return rep["body"] == f"echo:hi from {i} to {j}" and rep["ok"]
        tasks = [one(i, j) for i in range(N) for j in range(N) if i != j]
        results = await asyncio.gather(*tasks)
        check(all(results), f"full mesh {len(results)} concurrent asks all correct")

        # --- 100 rapid asks 0->1 ---
        rapid = await asyncio.gather(*[clients[0].ask(ids[1], f"q{k}", f"r{k}") for k in range(100)])
        check(all(r["body"] == f"echo:q{k}" for k, r in enumerate(rapid)),
              "100 concurrent asks 0->1 correlated correctly")

        # --- large payload (256 KB) ---
        big = "X" * (256 * 1024)
        rep = await clients[2].ask(ids[3], big, "big-1", timeout=20)
        check(rep["ok"] and rep["body"] == "echo:" + big, "256 KB payload round-trips intact")

        # --- unknown target -> ok:false ---
        rep = await clients[0].ask("s999", "nobody home", "u-1")
        check(rep["ok"] is False, "ask to unknown target returns ok=false (no hang)")

        # --- send (fire-and-forget) + control event delivery ---
        await clients[0].send({"cmd": "send", "target": ids[1], "body": "ping"})
        ev = await asyncio.wait_for(clients[1].events.get(), 5)
        # may receive a queued 'peers' first; drain to the message
        while ev.get("event") != "message":
            ev = await asyncio.wait_for(clients[1].events.get(), 5)
        check(ev.get("body") == "ping", "fire-and-forget 'send' delivered as message")

        # --- disconnect propagation: drop client4, others get a peers update ---
        # clear queued events first
        for c in clients:
            while not c.events.empty():
                c.events.get_nowait()
        await clients[4].close()
        ev = await asyncio.wait_for(clients[0].events.get(), 5)
        while ev.get("event") != "peers":
            ev = await asyncio.wait_for(clients[0].events.get(), 5)
        local_ids = {s["session_id"] for s in ev.get("local", [])}
        check(ids[4] not in local_ids, "peers list updates after a session disconnects")

        # --- asking the now-gone session returns ok:false ---
        rep = await clients[0].ask(ids[4], "you there?", "gone-1")
        check(rep["ok"] is False, "ask to disconnected session returns ok=false")

        for c in clients[:4]:
            await c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if FAILS:
        print(f"STRESS FAILED — {len(FAILS)} issue(s): {FAILS}")
        sys.exit(1)
    print("STRESS OK — all routing/lifecycle checks passed")


if __name__ == "__main__":
    asyncio.run(run())
