"""Smoke test: start the relay, register two nodes, and verify routing.

Exercises register -> connect_request -> connect_response -> relay envelope
forwarding between two outbound-connected nodes. Run from the repo root:

    PYTHONPATH=. python tests/smoke_relay.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import websockets

PORT = 8799
URL = f"ws://127.0.0.1:{PORT}"


async def connect_node(node_id: str, label: str):
    ws = await websockets.connect(URL)
    await ws.send(json.dumps({"type": "register", "node_id": node_id, "pubkey": "pk-" + node_id, "label": label}))
    ack = json.loads(await ws.recv())
    assert ack["type"] == "registered" and ack["node_id"] == node_id, ack
    return ws


async def run() -> None:
    proc = subprocess.Popen([sys.executable, "-m", "agentsync.relay.server", "--port", str(PORT)])
    try:
        # Wait for the relay to accept connections.
        a = None
        for _ in range(50):
            try:
                a = await connect_node("AS-AAAA-0001", "node-a")
                break
            except OSError:
                await asyncio.sleep(0.1)
        assert a is not None, "relay did not come up"
        b = await connect_node("AS-BBBB-0002", "node-b")
        print("registered both nodes")

        # A -> connect_request -> B
        await a.send(json.dumps({
            "type": "connect_request", "from_node": "AS-AAAA-0001", "to_node": "AS-BBBB-0002",
            "from_label": "node-a", "request_id": "r1", "from_pubkey": "pk-a",
        }))
        req = json.loads(await b.recv())
        assert req["type"] == "connect_request" and req["request_id"] == "r1", req
        print("connect_request routed A -> B")

        # B -> connect_response (accepted) -> A
        await b.send(json.dumps({
            "type": "connect_response", "request_id": "r1", "from_node": "AS-AAAA-0001",
            "to_node": "AS-AAAA-0001", "accepted": True, "to_pubkey": "pk-b", "reason": "",
        }))
        resp = json.loads(await a.recv())
        assert resp["type"] == "connect_response" and resp["accepted"] is True, resp
        print("connect_response routed B -> A")

        # A -> encrypted relay envelope -> B (relay can't read 'box')
        await a.send(json.dumps({
            "type": "relay", "from_node": "AS-AAAA-0001", "to_node": "AS-BBBB-0002", "box": "ciphertext-blob",
        }))
        env = json.loads(await b.recv())
        assert env["type"] == "relay" and env["box"] == "ciphertext-blob", env
        print("encrypted relay envelope routed A -> B")

        # Error path: send to an unknown peer.
        await a.send(json.dumps({
            "type": "relay", "from_node": "AS-AAAA-0001", "to_node": "AS-NOPE-9999", "box": "x",
        }))
        err = json.loads(await a.recv())
        assert err["type"] == "error", err
        print("error returned for offline peer")

        await a.close()
        await b.close()
        print("\nSMOKE OK — relay routing works")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(run())
