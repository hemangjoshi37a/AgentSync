"""Test To/CC/BCC selective multi-recipient delivery.

Proves the privacy/token-saving guarantee: only addressed sessions receive a
message, BCC recipients get it but are hidden from the visible To/CC audience,
and unaddressed sessions receive nothing. Run from the repo root:

    PYTHONPATH=. python tests/test_groupmsg.py
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
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class Client:
    def __init__(self, reader, writer, sid):
        self.r, self.w, self.sid = reader, writer, sid
        self.messages: list[dict] = []
        self._task = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while True:
                line = await self.r.readline()
                if not line:
                    break
                m = json.loads(line)
                if m.get("event") == "message":
                    self.messages.append(m)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass

    async def send(self, obj):
        self.w.write((json.dumps(obj) + "\n").encode())
        await self.w.drain()


async def connect(sock, label) -> Client:
    r, w = await asyncio.open_unix_connection(sock, limit=16 * 1024 * 1024)
    w.write((json.dumps({"cmd": "hello", "label": label, "role": "session"}) + "\n").encode())
    await w.drain()
    while True:
        line = await r.readline()
        m = json.loads(line)
        if m.get("event") == "welcome":
            return Client(r, w, m["session_id"])


async def run() -> None:
    home = tempfile.mkdtemp(prefix="as-grp-")
    env = dict(os.environ, AGENTSYNC_HOME=home, AGENTSYNC_NO_RELAY="1")
    proc = subprocess.Popen([sys.executable, "-m", "agentsync.daemon"], env=env)
    sock = os.path.join(home, "daemon.sock")
    try:
        for _ in range(50):
            if os.path.exists(sock):
                try:
                    t = await asyncio.open_unix_connection(sock)
                    t[1].close()
                    break
                except OSError:
                    pass
            await asyncio.sleep(0.1)

        sender = await connect(sock, "sender")
        r1 = await connect(sock, "r1")
        r2 = await connect(sock, "r2")
        r3 = await connect(sock, "r3")
        r4 = await connect(sock, "r4")
        print(f"sender={sender.sid} r1={r1.sid} r2={r2.sid} r3={r3.sid} r4={r4.sid}")

        # To=r1, CC=r2, BCC=r3 ; r4 not addressed at all.
        await sender.send({
            "cmd": "send", "to": [r1.sid], "cc": [r2.sid], "bcc": [r3.sid], "body": "secret-plan",
        })
        await asyncio.sleep(0.6)  # allow async delivery

        check(len(r1.messages) == 1 and r1.messages[0]["body"] == "secret-plan", "r1 (To) received it")
        check(len(r2.messages) == 1 and r2.messages[0]["body"] == "secret-plan", "r2 (CC) received it")
        check(len(r3.messages) == 1 and r3.messages[0]["body"] == "secret-plan", "r3 (BCC) received it")
        check(len(r4.messages) == 0, "r4 (not addressed) received NOTHING — selective delivery saves its tokens")

        msg = r1.messages[0]
        check(msg.get("to") == [r1.sid] and msg.get("cc") == [r2.sid], "visible audience is To=[r1], CC=[r2]")
        visible = list(msg.get("to", [])) + list(msg.get("cc", []))
        check(r3.sid not in visible, "BCC recipient r3 is hidden from the visible To/CC")
        check(r3.messages[0].get("to") == [r1.sid] and r3.sid not in (
            list(r3.messages[0].get("to", [])) + list(r3.messages[0].get("cc", []))
        ), "even r3's own copy does not reveal it was BCC'd")

        # Backward-compat: legacy single-target send.
        await sender.send({"cmd": "send", "target": r4.sid, "body": "legacy"})
        await asyncio.sleep(0.5)
        check(len(r4.messages) == 1 and r4.messages[0]["body"] == "legacy", "legacy single-target send still works")

        print("\nGROUPMSG OK — selective To/CC/BCC delivery works" if not FAILS
              else f"\nGROUPMSG FAILED: {FAILS}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(run())
