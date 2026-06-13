"""AgentSync daemon — local Unix-socket hub + relay gateway + router.

One daemon runs per machine. It:

* listens on a Unix-domain socket for **local clients** (Claude sessions via
  the plugin's MCP server, the headless responder, and the TUI control client);
* maintains one **outbound** WebSocket to the relay and registers this node;
* routes peer-layer messages: if the target is a local session it is delivered
  over the socket; if it is a remote node id it is sealed (end-to-end encrypted)
  and forwarded through the relay.

The wire contract for the local socket is documented in docs/PROTOCOL.md — keep
this implementation and that document in lockstep.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid

import websockets

from . import config as C
from . import crypto
from . import protocol as P

log = logging.getLogger("agentsync.daemon")


def _auto_accept_remote() -> bool:
    """Test/headless escape hatch: auto-accept remote connects with no TUI."""
    return os.environ.get("AGENTSYNC_AUTO_ACCEPT") == "1"


def _is_remote(target: object) -> bool:
    return isinstance(target, str) and target.startswith("AS-")


class Session:
    """A connected local client (a Claude session or the control TUI)."""

    def __init__(self, sid: str, writer: asyncio.StreamWriter, label: str, role: str) -> None:
        self.id = sid
        self.writer = writer
        self.label = label
        self.role = role
        self._lock = asyncio.Lock()

    async def send(self, obj: dict) -> None:
        async with self._lock:
            self.writer.write(P.frame(obj))
            await self.writer.drain()


class RemotePeer:
    """An active, consented connection to a remote node."""

    def __init__(self, node_id: str, label: str, box) -> None:
        self.node_id = node_id
        self.label = label
        self.box = box
        self.paused = False


class Daemon:
    def __init__(self, cfg: C.Config, priv) -> None:
        self.cfg = cfg
        self.priv = priv
        self.node_id = cfg.node_id
        self.sessions: dict[str, Session] = {}
        self.peers: dict[str, RemotePeer] = {}
        self.relay_ws = None
        # request_id -> ("local", Session) | ("remote", node_id)
        self.pending_asks: dict[str, tuple[str, object]] = {}
        # request_id -> (origin Session, target node id) for outbound connects
        self.pending_connect: dict[str, tuple[Session, str]] = {}
        # request_id -> inbound connect_request awaiting consent
        self.pending_consent: dict[str, dict] = {}
        self._counter = 0

    # ---- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        path = str(C.SOCKET_PATH)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(self._on_client, path=path)
        os.chmod(path, 0o600)
        C.PIDFILE.write_text(str(os.getpid()))
        if not os.environ.get("AGENTSYNC_NO_RELAY"):
            asyncio.create_task(self._relay_loop())
        log.info("daemon up: node %s, label %r, socket %s", self.node_id, self.cfg.label, path)
        try:
            async with server:
                await server.serve_forever()
        finally:
            for _p in (C.PIDFILE, C.SOCKET_PATH):
                try:
                    os.unlink(_p)
                except FileNotFoundError:
                    pass

    # ---- local socket clients ----------------------------------------------

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session: Session | None = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    cmd = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                session = await self._dispatch(cmd, session, writer)
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        finally:
            if session is not None:
                await self._remove_session(session)

    async def _dispatch(self, cmd: dict, session: Session | None, writer) -> Session | None:
        c = cmd.get("cmd")

        if c == "hello":
            self._counter += 1
            sid = f"s{self._counter}"
            session = Session(sid, writer, str(cmd.get("label", "claude")), str(cmd.get("role", "session")))
            self.sessions[sid] = session
            await session.send({"event": "welcome", "session_id": sid, "node_id": self.node_id, "label": session.label})
            await self._broadcast_peers()
            return session

        if session is None:
            return None  # ignore anything before hello

        if c in ("peers", "status"):
            await session.send(self._peers_payload())
        elif c == "ask":
            await self._handle_ask(session, cmd)
        elif c == "reply":
            await self._handle_reply(session, cmd)
        elif c == "send":
            await self._handle_send(session, cmd)
        elif c == "connect":
            await self._handle_connect(session, cmd)
        elif c == "control":
            await self._handle_control(session, cmd)
        elif c == "accept":
            await self._handle_accept(str(cmd.get("request_id")), bool(cmd.get("remember", False)))
        elif c == "reject":
            await self._handle_reject(str(cmd.get("request_id")), "rejected by user")
        elif c == "trust":
            await self._handle_trust(session, cmd)
        elif c == "untrust":
            await self._handle_untrust(session, cmd)
        else:
            await session.send({"event": "error", "message": f"unknown command {c!r}"})
        return session

    async def _remove_session(self, session: Session) -> None:
        self.sessions.pop(session.id, None)
        try:
            session.writer.close()
        except Exception:
            pass
        await self._broadcast_peers()

    def _peers_payload(self) -> dict:
        local = [
            {"session_id": s.id, "label": s.label}
            for s in self.sessions.values()
            if s.role == "session"
        ]
        remote = [
            {"node_id": p.node_id, "label": p.label, "paused": p.paused}
            for p in self.peers.values()
        ]
        return {"event": "peers", "node_id": self.node_id, "local": local, "remote": remote}

    async def _broadcast_peers(self) -> None:
        payload = self._peers_payload()
        for s in list(self.sessions.values()):
            try:
                await s.send(payload)
            except Exception:
                pass

    # ---- ask / reply / send / control --------------------------------------

    async def _handle_ask(self, session: Session, cmd: dict) -> None:
        target = cmd.get("target")
        prompt = str(cmd.get("prompt", ""))
        rid = str(cmd.get("request_id") or uuid.uuid4().hex)

        if _is_remote(target):
            peer = self.peers.get(target)  # type: ignore[arg-type]
            if peer is None:
                await session.send({"event": "reply", "request_id": rid, "ok": False, "body": f"not connected to {target}"})
                return
            if peer.paused:
                await session.send({"event": "reply", "request_id": rid, "ok": False, "body": "bridge is paused"})
                return
            self.pending_asks[rid] = ("local", session)
            await self._relay_send(peer, P.ask(rid, prompt, session.label))
        else:
            dest = self.sessions.get(target)  # type: ignore[arg-type]
            if dest is None or dest.role != "session":
                await session.send({"event": "reply", "request_id": rid, "ok": False, "body": f"no local session {target!r}"})
                return
            self.pending_asks[rid] = ("local", session)
            await dest.send({"event": "ask", "request_id": rid, "from": session.id, "from_label": session.label, "prompt": prompt})

    async def _handle_reply(self, session: Session, cmd: dict) -> None:
        rid = str(cmd.get("request_id"))
        body = str(cmd.get("body", ""))
        ok = bool(cmd.get("ok", True))
        origin = self.pending_asks.pop(rid, None)
        if origin is None:
            return
        kind, ref = origin
        if kind == "local":
            await ref.send({"event": "reply", "request_id": rid, "ok": ok, "body": body})  # type: ignore[union-attr]
        else:  # remote
            peer = self.peers.get(ref)  # type: ignore[arg-type]
            if peer is not None:
                await self._relay_send(peer, P.reply(rid, body, ok))

    async def _handle_send(self, session: Session, cmd: dict) -> None:
        target = cmd.get("target")
        body = str(cmd.get("body", ""))
        if _is_remote(target):
            peer = self.peers.get(target)  # type: ignore[arg-type]
            if peer is not None and not peer.paused:
                await self._relay_send(peer, P.msg(body))
        else:
            dest = self.sessions.get(target)  # type: ignore[arg-type]
            if dest is not None:
                await dest.send({"event": "message", "from": session.id, "from_label": session.label, "body": body})

    async def _handle_control(self, session: Session, cmd: dict) -> None:
        target = cmd.get("target")
        action = str(cmd.get("action", ""))
        if _is_remote(target):
            peer = self.peers.get(target)  # type: ignore[arg-type]
            if peer is None:
                return
            if action == P.PAUSE:
                peer.paused = True
            elif action == P.RESUME:
                peer.paused = False
            await self._relay_send(peer, P.control(action))
            if action == P.STOP:
                self.peers.pop(peer.node_id, None)
                await self._broadcast_peers()
        else:
            dest = self.sessions.get(target)  # type: ignore[arg-type]
            if dest is not None:
                await dest.send({"event": "control", "from": session.id, "action": action})

    # ---- outbound connect (this node initiates) -----------------------------

    async def _handle_connect(self, session: Session, cmd: dict) -> None:
        target = cmd.get("target")

        if not _is_remote(target):
            dest = self.sessions.get(target)  # type: ignore[arg-type]
            ok = dest is not None and dest.role == "session"
            await session.send({
                "event": "connect_result", "ok": ok, "peer": target,
                "reason": "" if ok else "no such local session",
            })
            if ok:
                await session.send({"event": "connected", "peer": target, "label": dest.label})  # type: ignore[union-attr]
            return

        if self.relay_ws is None:
            await session.send({"event": "connect_result", "ok": False, "peer": target, "reason": "relay offline"})
            return

        rid = uuid.uuid4().hex
        self.pending_connect[rid] = (session, target)  # type: ignore[index]
        await self.relay_ws.send(P.dumps(P.connect_request(
            self.node_id, target, self.cfg.label, rid, crypto.public_b64(self.priv),  # type: ignore[arg-type]
        )))
        await session.send({"event": "connecting", "peer": target, "request_id": rid})

    # ---- consent (this node receives a request) -----------------------------

    def _is_trusted(self, node: str) -> bool:
        """A peer is auto-accepted if globally trusted or on the persisted list."""
        pol = self.cfg.policy
        return _auto_accept_remote() or pol.trust_all_remote or node in pol.trusted_nodes

    async def _on_connect_request(self, m: dict) -> None:
        rid = str(m["request_id"])
        from_node = str(m.get("from_node", ""))
        self.pending_consent[rid] = m
        # A persisted trusted peer (or global auto-accept) bypasses the prompt.
        if self._is_trusted(from_node):
            log.info("auto-accepting trusted peer %s", from_node)
            await self._handle_accept(rid)
            return
        controls = [s for s in self.sessions.values() if s.role == "control"]
        if controls and self.cfg.policy.require_consent_remote:
            for s in controls:
                await s.send({
                    "event": "incoming_connect", "request_id": rid,
                    "from_node": m.get("from_node"), "from_label": m.get("from_label", ""),
                })
        elif self.cfg.policy.require_consent_remote:
            # consent required but no TUI to grant it
            await self._handle_reject(rid, "no operator present to grant consent")
        else:
            await self._handle_accept(rid)

    async def _handle_accept(self, rid: str, remember: bool = False) -> None:
        m = self.pending_consent.pop(rid, None)
        if m is None:
            return
        frm = str(m["from_node"])
        if remember and frm not in self.cfg.policy.trusted_nodes:
            self.cfg.policy.trusted_nodes.append(frm)
            C.save(self.cfg)
            log.info("persisted trust for %s", frm)
        box = crypto.make_box(self.priv, m["from_pubkey"])
        self.peers[frm] = RemotePeer(frm, str(m.get("from_label", frm)), box)
        if self.relay_ws is not None:
            await self.relay_ws.send(P.dumps(P.connect_response(
                rid, frm, frm, True, crypto.public_b64(self.priv),
            )))
        for s in self.sessions.values():
            await s.send({"event": "connected", "peer": frm, "label": m.get("from_label", frm)})

    async def _handle_reject(self, rid: str, reason: str) -> None:
        m = self.pending_consent.pop(rid, None)
        if m is None:
            return
        if self.relay_ws is not None:
            await self.relay_ws.send(P.dumps(P.connect_response(
                rid, str(m["from_node"]), str(m["from_node"]), False, None, reason,
            )))

    async def _handle_trust(self, session: Session, cmd: dict) -> None:
        pol = self.cfg.policy
        if cmd.get("all"):
            pol.trust_all_remote = True
            C.save(self.cfg)
            await session.send({"event": "trusted", "all": True})
            return
        node = str(cmd.get("node", ""))
        if node and node not in pol.trusted_nodes:
            pol.trusted_nodes.append(node)
            C.save(self.cfg)
        await session.send({"event": "trusted", "node": node, "trusted_nodes": pol.trusted_nodes})

    async def _handle_untrust(self, session: Session, cmd: dict) -> None:
        pol = self.cfg.policy
        if cmd.get("all"):
            pol.trust_all_remote = False
            C.save(self.cfg)
            await session.send({"event": "untrusted", "all": True})
            return
        node = str(cmd.get("node", ""))
        if node in pol.trusted_nodes:
            pol.trusted_nodes.remove(node)
            C.save(self.cfg)
        await session.send({"event": "untrusted", "node": node, "trusted_nodes": pol.trusted_nodes})

    async def _on_connect_response(self, m: dict) -> None:
        rid = str(m["request_id"])
        entry = self.pending_connect.pop(rid, None)
        if entry is None:
            return
        session, target = entry
        if m.get("accepted"):
            box = crypto.make_box(self.priv, m["to_pubkey"])
            self.peers[target] = RemotePeer(target, target, box)
            await session.send({"event": "connect_result", "ok": True, "peer": target, "reason": ""})
            await session.send({"event": "connected", "peer": target, "label": target})
        else:
            await session.send({"event": "connect_result", "ok": False, "peer": target, "reason": str(m.get("reason", "rejected"))})

    # ---- relay connection ---------------------------------------------------

    async def _relay_send(self, peer: RemotePeer, peer_msg: dict) -> None:
        if self.relay_ws is None:
            return
        sealed = crypto.seal(peer.box, peer_msg)
        await self.relay_ws.send(P.dumps(P.relay_envelope(self.node_id, peer.node_id, sealed)))

    async def _relay_loop(self) -> None:
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.cfg.relay_url) as ws:
                    self.relay_ws = ws
                    await ws.send(P.dumps(P.register(self.node_id, crypto.public_b64(self.priv), self.cfg.label)))
                    ack = P.loads(await ws.recv())
                    log.info("relay registered: %s", ack.get("type"))
                    backoff = 1
                    async for raw in ws:
                        await self._on_relay(P.loads(raw))
            except Exception as exc:  # noqa: BLE001 - keep retrying on any failure
                log.warning("relay connection lost: %s (retry in %ss)", exc, backoff)
            self.relay_ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _on_relay(self, m: dict) -> None:
        t = m.get("type")
        if t == P.CONNECT_REQUEST:
            await self._on_connect_request(m)
        elif t == P.CONNECT_RESPONSE:
            await self._on_connect_response(m)
        elif t == P.RELAY:
            peer = self.peers.get(str(m.get("from_node")))
            if peer is None:
                return
            try:
                payload = crypto.unseal(peer.box, m["box"])
            except Exception:
                log.warning("failed to decrypt payload from %s", m.get("from_node"))
                return
            await self._on_peer_message(peer, payload)
        elif t == P.PEER_GONE:
            nid = str(m.get("node_id"))
            if self.peers.pop(nid, None) is not None:
                for s in self.sessions.values():
                    await s.send({"event": "peer_gone", "peer": nid})
        elif t == P.ERROR:
            log.warning("relay error: %s", m.get("message"))

    async def _on_peer_message(self, peer: RemotePeer, msg: dict) -> None:
        t = msg.get("type")
        if t == P.ASK:
            rid = str(msg["request_id"])
            targets = [s for s in self.sessions.values() if s.role == "session"]
            if not targets:
                await self._relay_send(peer, P.reply(rid, "no active Claude session on this node", False))
                return
            self.pending_asks[rid] = ("remote", peer.node_id)
            for s in targets:
                await s.send({
                    "event": "ask", "request_id": rid, "from": peer.node_id,
                    "from_label": peer.label, "prompt": msg.get("prompt", ""),
                })
        elif t == P.REPLY:
            rid = str(msg["request_id"])
            origin = self.pending_asks.pop(rid, None)
            if origin is not None and origin[0] == "local":
                await origin[1].send({  # type: ignore[union-attr]
                    "event": "reply", "request_id": rid,
                    "ok": bool(msg.get("ok", True)), "body": msg.get("body", ""),
                })
        elif t == P.MSG:
            for s in self.sessions.values():
                if s.role == "session":
                    await s.send({"event": "message", "from": peer.node_id, "from_label": peer.label, "body": msg.get("body", "")})
        elif t == P.CONTROL:
            action = str(msg.get("action", ""))
            if action == P.PAUSE:
                peer.paused = True
            elif action == P.RESUME:
                peer.paused = False
            for s in self.sessions.values():
                await s.send({"event": "control", "from": peer.node_id, "action": action})


def main() -> None:
    ap = argparse.ArgumentParser(description="AgentSync node daemon")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg, priv = C.load_or_create()
    try:
        asyncio.run(Daemon(cfg, priv).start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
