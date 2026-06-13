"""Smoke test: persistent trusted-peer consent.

Verifies the daemon's trust/untrust commands update the in-memory policy AND
persist to config.toml, so trusted peers auto-accept across restarts. Run from
the repo root:

    PYTHONPATH=. python tests/smoke_trust.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import tomllib

NODE = "AS-TEST-9999"


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


def trusted_in_config(home: str) -> list[str]:
    cfg = tomllib.loads(open(os.path.join(home, "config.toml")).read())
    return cfg.get("policy", {}).get("trusted_nodes", [])


async def run() -> None:
    home = tempfile.mkdtemp(prefix="as-trust-")
    env = dict(os.environ, AGENTSYNC_HOME=home, AGENTSYNC_NO_RELAY="1")
    proc = subprocess.Popen([sys.executable, "-m", "agentsync.daemon"], env=env)
    sock = os.path.join(home, "daemon.sock")
    try:
        r = w = None
        for _ in range(50):
            if os.path.exists(sock):
                try:
                    r, w = await asyncio.open_unix_connection(sock)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
            await asyncio.sleep(0.1)
        assert r is not None and w is not None, "daemon socket never came up"

        await send(w, {"cmd": "hello", "label": "cli", "role": "control"})
        await recv_event(r, "welcome")

        await send(w, {"cmd": "trust", "node": NODE})
        ev = await recv_event(r, "trusted")
        assert NODE in ev.get("trusted_nodes", []), ev
        assert NODE in trusted_in_config(home), "not persisted to config.toml"
        print(f"trust {NODE}: in-memory + persisted to config.toml ✓")

        await send(w, {"cmd": "untrust", "node": NODE})
        ev2 = await recv_event(r, "untrusted")
        assert NODE not in ev2.get("trusted_nodes", []), ev2
        assert NODE not in trusted_in_config(home), "untrust not persisted"
        print(f"untrust {NODE}: removed from memory + config.toml ✓")

        await send(w, {"cmd": "trust", "all": True})
        ev3 = await recv_event(r, "trusted")
        assert ev3.get("all") is True, ev3
        cfg = tomllib.loads(open(os.path.join(home, "config.toml")).read())
        assert cfg["policy"]["trust_all_remote"] is True, cfg["policy"]
        print("trust --all: trust_all_remote persisted ✓")

        print("\nSMOKE OK — trusted-peer persistence works")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(run())
