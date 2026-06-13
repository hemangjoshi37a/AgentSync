#!/usr/bin/env python3
"""AgentSync MCP server — the ``agentsync_*`` tools for a Claude Code session.

This is a **self-contained** stdio MCP server that ships inside the AgentSync
Claude Code plugin. It is launched by Claude Code as::

    python3 ${CLAUDE_PLUGIN_ROOT}/mcp/server.py

It maintains a single persistent asyncio connection to the local AgentSync
daemon over a Unix-domain socket and exposes the bridge as MCP tools.

Design constraints:
  * Pure **standard library** — NO third-party dependencies (not even the
    ``mcp`` SDK). It speaks the MCP stdio protocol (JSON-RPC 2.0, newline-
    delimited) directly, and does NOT import the ``agentsync`` package. So
    installing the plugin is the ONLY setup a user needs — ``python3`` is
    already present wherever Claude Code runs.
  * stdout is the MCP JSON-RPC channel — all logging goes to **stderr**.

Protocol (newline-delimited JSON over the Unix socket) is documented in
``docs/PROTOCOL.md``; this file must stay in sync with that contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Logging — stderr only. stdout belongs to the MCP JSON-RPC transport.
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=os.environ.get("AGENTSYNC_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s agentsync-mcp %(levelname)s %(message)s",
)
log = logging.getLogger("agentsync.mcp")


# --------------------------------------------------------------------------- #
# Inlined configuration (do NOT import agentsync).
# --------------------------------------------------------------------------- #
def _socket_path() -> str:
    """Resolve the daemon socket path using stdlib only.

    Precedence:
      1. ``AGENTSYNC_SOCKET`` — explicit socket path.
      2. ``$AGENTSYNC_HOME/daemon.sock`` — when ``AGENTSYNC_HOME`` is set.
      3. ``~/.agentsync/daemon.sock`` — the default.
    """
    explicit = os.environ.get("AGENTSYNC_SOCKET")
    if explicit:
        return explicit
    home = os.environ.get("AGENTSYNC_HOME")
    if home:
        return str(Path(home).expanduser() / "daemon.sock")
    return str(Path.home() / ".agentsync" / "daemon.sock")


def _session_label() -> str:
    """Resolve this session's human label.

    ``AGENTSYNC_LABEL`` if set, else the basename of the current working dir.
    """
    label = os.environ.get("AGENTSYNC_LABEL")
    if label:
        return label
    return os.path.basename(os.getcwd()) or "session"


SOCKET_PATH = _socket_path()
SESSION_LABEL = _session_label()


# --------------------------------------------------------------------------- #
# Daemon connection — one persistent connection with a background reader.
# --------------------------------------------------------------------------- #
class DaemonClient:
    """A single persistent, lazily-established connection to the daemon.

    Owns the reader/writer pair, a background read loop that dispatches daemon
    events, the in-memory inboxes, and the futures used to await replies and
    connect results.
    """

    def __init__(self, socket_path: str, label: str) -> None:
        self.socket_path = socket_path
        self.label = label

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None

        # Serialise the (re)connect handshake and guard socket writes.
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

        # Identity from the latest `welcome` event.
        self.node_id: str | None = None
        self.session_id: str | None = None

        # Latest snapshot from the most recent `peers`/`status` event.
        self.peers_snapshot: dict[str, Any] = {"local": [], "remote": []}
        self._peers_event = asyncio.Event()

        # Pending request/response futures.
        self._reply_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._connect_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

        # In-memory inboxes.
        self.inbox_asks: list[dict[str, Any]] = []
        self.inbox_messages: list[dict[str, Any]] = []

    # -- connection lifecycle ---------------------------------------------- #
    async def ensure_connected(self) -> None:
        """Establish the connection (and reader task) if not already up."""
        if self._writer is not None and not self._writer.is_closing():
            return
        async with self._connect_lock:
            # Re-check inside the lock: another coroutine may have connected.
            if self._writer is not None and not self._writer.is_closing():
                return
            await self._connect_locked()

    async def _ensure_daemon_running(self) -> None:
        """Start the bundled daemon if the socket is not live (zero-setup)."""
        try:
            import sys as _sys
            from pathlib import Path as _Path

            runtime = _Path(__file__).resolve().parent.parent / "runtime"
            if str(runtime) not in _sys.path:
                _sys.path.insert(0, str(runtime))
            import bootstrap  # bundled in plugin/runtime/

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, bootstrap.ensure_daemon, str(runtime))
        except Exception as exc:  # never block tool use on bootstrap
            log.warning("daemon bootstrap skipped: %s", exc)

    async def _connect_locked(self) -> None:
        await self._ensure_daemon_running()
        log.info("connecting to daemon at %s", self.socket_path)
        reader, writer = await asyncio.open_unix_connection(self.socket_path, limit=16 * 1024 * 1024)
        self._reader = reader
        self._writer = writer

        # Send hello as the mandatory first message.
        await self._write_locked(
            {"cmd": "hello", "label": self.label, "role": "session", "cwd": os.getcwd()}
        )

        # Read the welcome event synchronously before starting the loop so the
        # caller has identity available immediately after connect.
        line = await reader.readline()
        if not line:
            raise ConnectionError("daemon closed connection before welcome")
        try:
            welcome = json.loads(line.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionError(f"invalid welcome from daemon: {exc!r}") from exc
        if welcome.get("event") == "welcome":
            self.session_id = welcome.get("session_id")
            self.node_id = welcome.get("node_id")
            if welcome.get("label"):
                self.label = welcome["label"]
            log.info(
                "welcomed: node_id=%s session_id=%s label=%s",
                self.node_id,
                self.session_id,
                self.label,
            )
        else:
            # Not fatal — dispatch it through the normal path so we don't drop
            # an early event the daemon may have sent.
            self._dispatch(welcome)

        # (Re)start the background reader.
        self._reader_task = asyncio.create_task(
            self._read_loop(reader), name="agentsync-daemon-reader"
        )

    async def _write_locked(self, obj: dict[str, Any]) -> None:
        """Write one framed JSON message. Caller must hold no special lock for
        the handshake path; general callers go through :meth:`send`."""
        writer = self._writer
        if writer is None:
            raise ConnectionError("not connected")
        writer.write(json.dumps(obj).encode() + b"\n")
        await writer.drain()

    async def send(self, obj: dict[str, Any]) -> None:
        """Public, lock-guarded send used by tools. Reconnects on demand."""
        await self.ensure_connected()
        async with self._write_lock:
            try:
                await self._write_locked(obj)
            except (ConnectionError, OSError) as exc:
                log.warning("write failed (%s); reconnecting and retrying", exc)
                # Drop the dead writer, reconnect, retry once.
                self._writer = None
        # If the write failed we reconnect outside the write lock to avoid
        # holding it across a handshake, then retry once.
        if self._writer is None:
            await self.ensure_connected()
            async with self._write_lock:
                await self._write_locked(obj)

    # -- background reader -------------------------------------------------- #
    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Read newline-framed JSON events forever and dispatch them.

        On EOF / error, fails any pending futures and triggers a reconnect so
        subsequent tool calls transparently re-establish the link.
        """
        try:
            while True:
                line = await reader.readline()
                if not line:
                    log.warning("daemon connection closed (EOF)")
                    break
                try:
                    event = json.loads(line.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    log.warning("dropping malformed line from daemon")
                    continue
                self._dispatch(event)
        except (asyncio.CancelledError, ConnectionError, OSError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            log.warning("reader loop error: %s", exc)
        finally:
            self._on_disconnect()

    def _on_disconnect(self) -> None:
        """Tear down state on disconnect; fail pending futures."""
        if self._writer is not None and not self._writer.is_closing():
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
        self._writer = None
        self._reader = None
        for fut in list(self._reply_futures.values()):
            if not fut.done():
                fut.set_exception(ConnectionError("daemon disconnected"))
        self._reply_futures.clear()
        for fut in list(self._connect_futures.values()):
            if not fut.done():
                fut.set_exception(ConnectionError("daemon disconnected"))
        self._connect_futures.clear()

    def _dispatch(self, event: dict[str, Any]) -> None:
        """Route a single daemon event to the right handler."""
        kind = event.get("event")

        if kind == "welcome":
            self.session_id = event.get("session_id")
            self.node_id = event.get("node_id")
            if event.get("label"):
                self.label = event["label"]

        elif kind in ("peers", "status"):
            self.peers_snapshot = {
                "node_id": event.get("node_id"),
                "local": event.get("local", []) or [],
                "remote": event.get("remote", []) or [],
            }
            self._peers_event.set()

        elif kind == "reply":
            rid = event.get("request_id")
            fut = self._reply_futures.pop(rid, None) if isinstance(rid, str) else None
            if fut is not None and not fut.done():
                fut.set_result(event)
            else:
                log.debug("reply for unknown request_id=%s", rid)

        elif kind == "ask":
            self.inbox_asks.append(
                {
                    "request_id": event.get("request_id"),
                    "from": event.get("from"),
                    "from_label": event.get("from_label"),
                    "prompt": event.get("prompt"),
                }
            )

        elif kind == "message":
            self.inbox_messages.append(
                {
                    "from": event.get("from"),
                    "from_label": event.get("from_label"),
                    "to": event.get("to", []),
                    "cc": event.get("cc", []),
                    "body": event.get("body"),
                }
            )

        elif kind == "connect_result":
            peer = event.get("peer")
            fut = self._connect_futures.pop(peer, None) if isinstance(peer, str) else None
            if fut is not None and not fut.done():
                fut.set_result(event)
            else:
                # No exact peer match — resolve any single pending future.
                if len(self._connect_futures) == 1:
                    _, only = self._connect_futures.popitem()
                    if not only.done():
                        only.set_result(event)
                else:
                    log.debug("connect_result for unmatched peer=%s", peer)

        elif kind == "connected":
            log.info("bridge active with %s (%s)", event.get("peer"),
                     event.get("label"))

        elif kind == "connecting":
            log.debug("connecting to %s (request_id=%s)",
                      event.get("peer"), event.get("request_id"))

        elif kind == "control":
            log.info("control from %s: %s", event.get("from"),
                     event.get("action"))

        elif kind == "peer_gone":
            log.info("peer gone: %s", event.get("peer"))

        elif kind == "error":
            log.warning("daemon error: %s", event.get("message"))

        else:
            log.debug("unhandled daemon event: %s", kind)

    # -- request/response helpers ------------------------------------------ #
    def new_reply_future(self, request_id: str) -> asyncio.Future[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._reply_futures[request_id] = fut
        return fut

    def new_connect_future(self, peer: str) -> asyncio.Future[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._connect_futures[peer] = fut
        return fut

    async def request_peers(self, timeout: float = 5.0) -> dict[str, Any]:
        """Ask the daemon for a fresh peer snapshot and return it."""
        self._peers_event.clear()
        await self.send({"cmd": "peers"})
        try:
            await asyncio.wait_for(self._peers_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.debug("peers request timed out; returning last snapshot")
        return self.peers_snapshot


# Module-level singleton; established lazily on first tool call.
_client = DaemonClient(SOCKET_PATH, SESSION_LABEL)


# --------------------------------------------------------------------------- #
# MCP server + tools.
# --------------------------------------------------------------------------- #
async def _connected_client() -> DaemonClient:
    """Return the singleton client, ensuring the daemon link is up."""
    await _client.ensure_connected()
    return _client


async def agentsync_whoami() -> dict[str, Any]:
    """Identify this Claude session on the AgentSync network.

    Returns this session's stable ``node_id`` (the machine-level AgentSync id,
    e.g. ``AS-7K3F-9210``), its local ``session_id`` (e.g. ``s1`` — how other
    local sessions address it), and its human-readable ``label``. Call this
    first to learn who you are before connecting or messaging peers.
    """
    try:
        c = await _connected_client()
        return {
            "node_id": c.node_id,
            "session_id": c.session_id,
            "label": c.label,
        }
    except Exception as exc:  # noqa: BLE001 — never raise out of a tool
        log.exception("agentsync_whoami failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_peers() -> dict[str, Any]:
    """List the AgentSync peers currently reachable from this session.

    Requests a fresh snapshot from the daemon and returns ``local`` (other
    Claude sessions on this same machine, each ``{session_id, label}`` —
    address them by ``session_id``) and ``remote`` (sessions on other machines,
    each ``{node_id, label, paused}`` — address them by ``node_id``, which
    starts with ``AS-``). Use this to discover whom you can ``connect`` to,
    ``ask``, or ``send`` messages.
    """
    try:
        c = await _connected_client()
        snap = await c.request_peers()
        return {
            "local": snap.get("local", []),
            "remote": snap.get("remote", []),
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_peers failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_connect(peer_id: str, timeout: float = 30) -> dict[str, Any]:
    """Open a bridge to a peer, performing the consent handshake if needed.

    ``peer_id`` is a remote ``node_id`` (starts with ``AS-``) or a local
    ``session_id``. Local peers usually auto-accept by policy; remote peers
    require the other side to consent, so this may block until they accept or
    reject (up to ``timeout`` seconds). Returns ``{ok, peer, reason}``. You
    must connect before ``ask``-ing or ``send``-ing to a remote peer.
    """
    try:
        c = await _connected_client()
        fut = c.new_connect_future(peer_id)
        await c.send({"cmd": "connect", "target": peer_id})
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            c._connect_futures.pop(peer_id, None)
            return {"ok": False, "peer": peer_id, "reason": "timeout"}
        return {
            "ok": bool(result.get("ok")),
            "peer": result.get("peer", peer_id),
            "reason": result.get("reason", ""),
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_connect failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_ask(peer: str | list[str], prompt: str, timeout: float = 120) -> dict[str, Any]:
    """Ask one or more peers a question and wait for the answer(s).

    ``peer`` may be a single id (local ``session_id`` or remote ``node_id``) —
    returns ``{ok, body}`` — or a LIST of ids — returns
    ``{ok: True, answers: [{peer, ok, body}, ...]}`` (peers asked concurrently).
    Blocks until each peer replies or ``timeout`` seconds elapse. Peers see the
    question in their inbox and answer via ``agentsync_respond``. For remote
    peers, ``connect`` first.
    """
    try:
        c = await _connected_client()

        async def _ask_one(target: str) -> dict[str, Any]:
            request_id = uuid.uuid4().hex
            fut = c.new_reply_future(request_id)
            await c.send({"cmd": "ask", "target": target, "prompt": prompt, "request_id": request_id})
            try:
                reply = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                c._reply_futures.pop(request_id, None)
                return {"peer": target, "ok": False, "error": "timeout"}
            return {"peer": target, "ok": bool(reply.get("ok", True)), "body": reply.get("body")}

        if isinstance(peer, (list, tuple)):
            answers = await asyncio.gather(*[_ask_one(str(p)) for p in peer])
            return {"ok": True, "answers": list(answers)}

        one = await _ask_one(str(peer))
        result: dict[str, Any] = {"ok": one["ok"]}
        if "body" in one:
            result["body"] = one.get("body")
        if "error" in one:
            result["error"] = one["error"]
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_ask failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_send(
    to: str | list[str],
    body: str,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
) -> dict[str, Any]:
    """Send a message to one or more peers, email-style (To / CC / BCC).

    ``to``, ``cc``, and ``bcc`` each accept a single peer id or a list of ids
    (local ``session_id`` or remote ``node_id``). Only the addressed peers
    receive the message — every other session gets nothing, saving their input
    tokens and processing time. Recipients see the To and CC audience; BCC
    recipients receive the body but are hidden from everyone (privacy). This is
    fire-and-forget; use ``agentsync_ask`` when you need a reply. Returns
    ``{ok: True}``.
    """
    try:
        c = await _connected_client()
        await c.send({"cmd": "send", "to": to, "cc": cc, "bcc": bcc, "body": body})
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_send failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_broadcast(
    body: str, exclude: str | list[str] | None = None
) -> dict[str, Any]:
    """Send a message to ALL currently-connected peers (local + remote).

    Optionally ``exclude`` a peer id or list of ids. Returns
    ``{ok: True, recipients: [...]}``. Prefer ``agentsync_send`` with an
    explicit To/CC list when only some peers need the message — broadcasting
    makes every connected session spend tokens reading it.
    """
    try:
        c = await _connected_client()
        snap = await c.request_peers()
        ex = {exclude} if isinstance(exclude, str) else set(exclude or [])
        targets: list[str] = []
        for s in snap.get("local", []):
            sid = s.get("session_id")
            if sid and sid != c.session_id and sid not in ex:
                targets.append(sid)
        for p in snap.get("remote", []):
            nid = p.get("node_id")
            if nid and nid not in ex:
                targets.append(nid)
        if not targets:
            return {"ok": True, "recipients": [], "note": "no connected peers"}
        await c.send({"cmd": "send", "to": targets, "body": body})
        return {"ok": True, "recipients": targets}
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_broadcast failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_inbox() -> dict[str, Any]:
    """Retrieve incoming asks and messages delivered to this session.

    Returns ``{asks, messages}``. ``asks`` are pending questions from peers,
    each ``{request_id, from, from_label, prompt}`` — these are **peeked**
    (left in the inbox) so you can answer them with ``agentsync_respond``
    using the ``request_id``. ``messages`` are fire-and-forget notes, each
    ``{from, from_label, body}`` — these are **drained** (returned once, then
    removed). Poll this to see what peers have sent you.
    """
    try:
        c = await _connected_client()
        # Peek asks (leave them so they can be responded to).
        asks = list(c.inbox_asks)
        # Drain messages.
        messages = list(c.inbox_messages)
        c.inbox_messages.clear()
        return {"asks": asks, "messages": messages}
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_inbox failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_respond(
    request_id: str, answer: str, ok: bool = True
) -> dict[str, Any]:
    """Answer a pending ask that was delivered to this session's inbox.

    ``request_id`` is the id of an ask from ``agentsync_inbox``; ``answer`` is
    the reply body sent back to the asking peer; set ``ok`` to ``False`` to
    signal failure/refusal. The matching ask is removed from the inbox.
    Returns ``{ok: True}`` once the reply is dispatched.
    """
    try:
        c = await _connected_client()
        await c.send(
            {
                "cmd": "reply",
                "request_id": request_id,
                "body": answer,
                "ok": ok,
            }
        )
        # Remove that ask from the inbox.
        c.inbox_asks = [
            a for a in c.inbox_asks if a.get("request_id") != request_id
        ]
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_respond failed")
        return {"ok": False, "error": str(exc)}


async def agentsync_control(peer: str, action: str) -> dict[str, Any]:
    """Control an active bridge with a peer: pause, resume, or stop it.

    ``peer`` is the local ``session_id`` or remote ``node_id`` of the bridged
    peer; ``action`` must be one of ``pause``, ``resume``, or ``stop``.
    ``pause`` suspends message flow, ``resume`` restores it, and ``stop`` tears
    the bridge down. Returns ``{ok: True}`` on dispatch, or an error if the
    action is invalid.
    """
    try:
        valid = {"pause", "resume", "stop"}
        if action not in valid:
            return {
                "ok": False,
                "error": f"invalid action {action!r}; expected one of {sorted(valid)}",
            }
        c = await _connected_client()
        await c.send({"cmd": "control", "target": peer, "action": action})
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        log.exception("agentsync_control failed")
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# MCP stdio protocol (JSON-RPC 2.0, newline-delimited) — pure standard library.
# --------------------------------------------------------------------------- #
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "agentsync-bridge", "version": "0.1.0"}

# A parameter that accepts a single id or a list of ids (To/CC/BCC, multi-ask).
_STR_OR_LIST = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}


def _tool(name: str, fn, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": (fn.__doc__ or "").strip(),
        "inputSchema": {"type": "object", "properties": properties, "required": required},
    }


TOOLS = [
    _tool("agentsync_whoami", agentsync_whoami, {}, []),
    _tool("agentsync_peers", agentsync_peers, {}, []),
    _tool("agentsync_connect", agentsync_connect,
          {"peer_id": {"type": "string"}, "timeout": {"type": "number"}}, ["peer_id"]),
    _tool("agentsync_ask", agentsync_ask,
          {"peer": _STR_OR_LIST, "prompt": {"type": "string"}, "timeout": {"type": "number"}},
          ["peer", "prompt"]),
    _tool("agentsync_send", agentsync_send,
          {"to": _STR_OR_LIST, "body": {"type": "string"}, "cc": _STR_OR_LIST, "bcc": _STR_OR_LIST},
          ["to", "body"]),
    _tool("agentsync_broadcast", agentsync_broadcast,
          {"body": {"type": "string"}, "exclude": _STR_OR_LIST}, ["body"]),
    _tool("agentsync_inbox", agentsync_inbox, {}, []),
    _tool("agentsync_respond", agentsync_respond,
          {"request_id": {"type": "string"}, "answer": {"type": "string"}, "ok": {"type": "boolean"}},
          ["request_id", "answer"]),
    _tool("agentsync_control", agentsync_control,
          {"peer": {"type": "string"},
           "action": {"type": "string", "enum": ["pause", "resume", "stop"]}},
          ["peer", "action"]),
]


async def _call_tool(name: str, args: dict[str, Any]) -> dict:
    # Required args use subscript (KeyError -> reported as a tool error);
    # optional args use .get with defaults.
    if name == "agentsync_whoami":
        return await agentsync_whoami()
    if name == "agentsync_peers":
        return await agentsync_peers()
    if name == "agentsync_connect":
        return await agentsync_connect(args["peer_id"], args.get("timeout", 30))
    if name == "agentsync_ask":
        return await agentsync_ask(args["peer"], args["prompt"], args.get("timeout", 120))
    if name == "agentsync_send":
        return await agentsync_send(args["to"], args["body"], args.get("cc"), args.get("bcc"))
    if name == "agentsync_broadcast":
        return await agentsync_broadcast(args["body"], args.get("exclude"))
    if name == "agentsync_inbox":
        return await agentsync_inbox()
    if name == "agentsync_respond":
        return await agentsync_respond(args["request_id"], args["answer"], args.get("ok", True))
    if name == "agentsync_control":
        return await agentsync_control(args["peer"], args["action"])
    raise ValueError(f"unknown tool: {name}")


async def _handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        pv = params.get("protocolVersion") or PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": pv, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            result = await _call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 — surface as a tool error, don't crash
            log.exception("tool %s failed", name)
            result = {"ok": False, "error": str(exc)}
        is_error = isinstance(result, dict) and result.get("ok") is False
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(result)}], "isError": is_error,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method and method.startswith("notifications/"):
        return None  # notifications get no response
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def _is_background_spare() -> bool:
    """True if launched under a pre-warmed Claude "background spare" process.

    Claude Code keeps spare processes ready; with alwaysLoad they each start this
    MCP server. They are NOT real sessions, so registering them would create
    phantom entries. Linux-only (/proc) check; elsewhere we assume a real session.
    """
    try:
        with open(f"/proc/{os.getppid()}/cmdline", "rb") as fh:
            cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
        return "--bg-spare" in cmdline
    except Exception:
        return False


async def _keepalive() -> None:
    """Register this real session with the daemon at startup, and re-register if
    the daemon restarts — so it appears in peers / the status line without a tool
    call. Background-spare processes are skipped to avoid phantom sessions."""
    if _is_background_spare():
        log.info("background-spare process; not registering with the daemon")
        return
    while True:
        try:
            await _connected_client()
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.debug("keepalive connect failed: %s", exc)
        await asyncio.sleep(15)


async def _serve_stdio() -> None:
    loop = asyncio.get_running_loop()
    log.info("agentsync MCP server up (stdlib stdio; socket=%s label=%s)", SOCKET_PATH, SESSION_LABEL)
    asyncio.create_task(_keepalive())
    while True:
        line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
        if not line:
            break  # stdin closed → Claude Code went away
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        try:
            resp = await _handle(req)
        except Exception:  # noqa: BLE001 — one bad request must not kill the server
            log.exception("error handling request")
            resp = None
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        pass
