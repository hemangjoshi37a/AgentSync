"""AgentSync relay — a minimal WebSocket rendezvous server.

Nodes dial OUT to this server and register by ID; it routes connect
requests, consent responses, and end-to-end-encrypted payloads between
paired nodes. The relay never sees plaintext conversation content — only
routing metadata (node IDs) and opaque ciphertext.

Because both peers connect outbound, this works through NAT, firewalls,
and one-directional VPNs (the AnyDesk model).

Run:  agentsync-relay --host 0.0.0.0 --port 8787
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

import websockets

from .. import protocol as P

log = logging.getLogger("agentsync.relay")


class Relay:
    """Routes messages between connected nodes, keyed by node id."""

    def __init__(self) -> None:
        self.nodes: dict[str, Any] = {}  # node_id -> websocket

    async def handler(self, ws: Any) -> None:
        node_id: str | None = None  # set once registered; used for cleanup
        try:
            # The first frame from a node must be a register.
            raw = await ws.recv()
            hello = P.loads(raw)
            if hello.get("type") != P.REGISTER:
                await ws.send(P.dumps(P.error("expected register as first message")))
                return

            nid = str(hello["node_id"])
            node_id = nid
            if self.nodes.get(nid) is not None:
                # A node id should be unique; drop the stale connection.
                log.warning("node %s reconnected; replacing previous connection", nid)
            self.nodes[nid] = ws
            log.info(
                "registered %s (%s) [%d online]",
                nid,
                hello.get("label", "?"),
                len(self.nodes),
            )
            await ws.send(P.dumps({"type": P.REGISTERED, "node_id": nid}))

            async for raw in ws:
                try:
                    message = P.loads(raw)
                except Exception:
                    await ws.send(P.dumps(P.error("malformed json")))
                    continue
                await self.route(nid, message)

        except websockets.ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001 - log and clean up any handler error
            log.exception("handler error: %s", exc)
        finally:
            if node_id and self.nodes.get(node_id) is ws:
                del self.nodes[node_id]
                log.info("disconnected %s [%d online]", node_id, len(self.nodes))
                await self.broadcast_gone(node_id)

    async def route(self, from_node: str, message: dict) -> None:
        mtype = message.get("type")

        if mtype in (P.CONNECT_REQUEST, P.CONNECT_RESPONSE, P.RELAY):
            to = message.get("to_node")
            dest = self.nodes.get(to) if isinstance(to, str) else None
            if dest is None:
                src = self.nodes.get(from_node)
                if src is not None:
                    await src.send(P.dumps(P.error(f"peer {to} is not online")))
                return
            await dest.send(P.dumps(message))

        elif mtype == P.PING:
            src = self.nodes.get(from_node)
            if src is not None:
                await src.send(P.dumps({"type": P.PONG}))

        else:
            log.warning("unknown message type from %s: %r", from_node, mtype)

    async def broadcast_gone(self, node_id: str) -> None:
        """Tell everyone a node left, so peers can tear down sessions."""
        for nid, ws in list(self.nodes.items()):
            try:
                await ws.send(P.dumps({"type": P.PEER_GONE, "node_id": node_id}))
            except websockets.ConnectionClosed:
                self.nodes.pop(nid, None)


async def serve(host: str, port: int) -> None:
    relay = Relay()
    log.info("AgentSync relay listening on ws://%s:%d", host, port)
    async with websockets.serve(relay.handler, host, port, ping_interval=20, ping_timeout=20):
        await asyncio.Future()  # run forever


def main() -> None:
    ap = argparse.ArgumentParser(description="AgentSync relay rendezvous server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
