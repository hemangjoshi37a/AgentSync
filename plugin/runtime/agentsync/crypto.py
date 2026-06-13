"""End-to-end encryption for the relay path (PyNaCl public-key boxes).

PyNaCl is imported **lazily** inside each function, so importing this module —
and therefore running the daemon in local-only mode — does NOT require PyNaCl
to be installed. PyNaCl is only needed for the remote/relay path (key exchange
+ box seal/open). This keeps same-machine bridging dependency-free.

NOTE (v0.1 threat model): public keys are exchanged through the relay, so a
malicious relay could substitute keys (a MITM). This is trust-on-first-use;
out-of-band fingerprint verification is on the roadmap.
"""

from __future__ import annotations

import base64
import json
from typing import Any


def generate_private_key() -> Any:
    from nacl.public import PrivateKey

    return PrivateKey.generate()


def load_private_key(b64: str) -> Any:
    from nacl.public import PrivateKey

    return PrivateKey(base64.b64decode(b64))


def dump_private_key(key: Any) -> str:
    return base64.b64encode(bytes(key)).decode()


def public_b64(priv: Any) -> str:
    return base64.b64encode(bytes(priv.public_key)).decode()


def make_box(priv: Any, peer_pub_b64: str) -> Any:
    from nacl.public import Box, PublicKey

    return Box(priv, PublicKey(base64.b64decode(peer_pub_b64)))


def seal(box: Any, obj: dict) -> str:
    data = json.dumps(obj, separators=(",", ":")).encode()
    return base64.b64encode(box.encrypt(data)).decode()


def unseal(box: Any, b64: str) -> dict:
    data = box.decrypt(base64.b64decode(b64))
    return json.loads(data)
