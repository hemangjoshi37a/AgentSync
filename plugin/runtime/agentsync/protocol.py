"""AgentSync wire protocol: message types, builders, and framing.

Two layers travel over the same connections:

* **Relay layer** — control/routing messages the relay itself reads
  (``register``, ``connect_request``, ``connect_response``, ``relay``,
  ``peer_gone``, ``error``). These carry only routing metadata, never
  conversation content.
* **Peer layer** — the actual agent-to-agent payload (``ask``, ``reply``,
  ``msg``, ``control``, ``presence``). On the relay path the peer layer is
  end-to-end encrypted inside a ``relay`` envelope's ``box`` field, so the
  relay never sees it. On the local Unix-socket path it travels as
  plaintext between sessions of the same user.

Framing: every message is a single JSON object. Over stream sockets (the
local Unix socket) we use newline-delimited JSON — one compact object per
line. WebSocket frames are already message-delimited, so no extra framing
is needed there.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1

# --- relay-layer message types (routing only; relay may read these) ---
REGISTER = "register"
REGISTERED = "registered"
CONNECT_REQUEST = "connect_request"
CONNECT_RESPONSE = "connect_response"
RELAY = "relay"  # opaque, end-to-end-encrypted peer payload
PEER_GONE = "peer_gone"
ERROR = "error"
PING = "ping"
PONG = "pong"

# --- peer-layer message types (inside RELAY.box, or local plaintext) ---
ASK = "ask"
REPLY = "reply"
MSG = "msg"
CONTROL = "control"  # action: pause | resume | stop
PRESENCE = "presence"

# control actions
PAUSE = "pause"
RESUME = "resume"
STOP = "stop"


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"))


def loads(raw: str | bytes) -> dict[str, Any]:
    return json.loads(raw)


def frame(obj: dict[str, Any]) -> bytes:
    """Newline-delimited JSON frame for stream sockets."""
    return (dumps(obj) + "\n").encode()


# --- builders: relay layer ---
def register(node_id: str, pubkey: str, label: str) -> dict:
    return {
        "type": REGISTER,
        "v": PROTOCOL_VERSION,
        "node_id": node_id,
        "pubkey": pubkey,
        "label": label,
    }


def connect_request(
    from_node: str, to_node: str, from_label: str, request_id: str, from_pubkey: str
) -> dict:
    return {
        "type": CONNECT_REQUEST,
        "from_node": from_node,
        "to_node": to_node,
        "from_label": from_label,
        "request_id": request_id,
        "from_pubkey": from_pubkey,
    }


def connect_response(
    request_id: str,
    from_node: str,
    to_node: str,
    accepted: bool,
    to_pubkey: str | None = None,
    reason: str = "",
) -> dict:
    return {
        "type": CONNECT_RESPONSE,
        "request_id": request_id,
        "from_node": from_node,
        "to_node": to_node,
        "accepted": accepted,
        "to_pubkey": to_pubkey,
        "reason": reason,
    }


def relay_envelope(from_node: str, to_node: str, box: str) -> dict:
    return {"type": RELAY, "from_node": from_node, "to_node": to_node, "box": box}


def error(message: str) -> dict:
    return {"type": ERROR, "message": message}


# --- builders: peer layer ---
def ask(request_id: str, prompt: str, from_label: str = "") -> dict:
    return {"type": ASK, "request_id": request_id, "prompt": prompt, "from_label": from_label}


def reply(request_id: str, body: str, ok: bool = True) -> dict:
    return {"type": REPLY, "request_id": request_id, "body": body, "ok": ok}


def msg(body: str) -> dict:
    return {"type": MSG, "body": body}


def multicast(body: str, from_label: str, to: list, cc: list) -> dict:
    """A message carrying its visible audience (to/cc). BCC recipients still
    receive this payload but are never listed in to/cc (privacy)."""
    return {"type": MSG, "body": body, "from_label": from_label, "to": to, "cc": cc}


def control(action: str) -> dict:
    return {"type": CONTROL, "action": action}
