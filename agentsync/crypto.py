"""End-to-end encryption for the relay path (PyNaCl public-key boxes).

Each node owns a Curve25519 keypair. Peers exchange public keys during the
consent handshake (the requester's key rides in ``connect_request``, the
accepter's in ``connect_response``). Each side then forms a ``Box`` from its
own private key + the peer's public key and uses it to seal/open peer-layer
messages. The relay only ever sees the ciphertext.

NOTE (v0.1 threat model): public keys are exchanged *through* the relay, so a
malicious relay could substitute keys (a MITM). This is trust-on-first-use.
Out-of-band key verification (compare a short fingerprint over a side channel)
is on the roadmap.
"""

from __future__ import annotations

import base64
import json

from nacl.public import Box, PrivateKey, PublicKey


def generate_private_key() -> PrivateKey:
    return PrivateKey.generate()


def load_private_key(b64: str) -> PrivateKey:
    return PrivateKey(base64.b64decode(b64))


def dump_private_key(key: PrivateKey) -> str:
    return base64.b64encode(bytes(key)).decode()


def public_b64(priv: PrivateKey) -> str:
    return base64.b64encode(bytes(priv.public_key)).decode()


def make_box(priv: PrivateKey, peer_pub_b64: str) -> Box:
    return Box(priv, PublicKey(base64.b64decode(peer_pub_b64)))


def seal(box: Box, obj: dict) -> str:
    data = json.dumps(obj, separators=(",", ":")).encode()
    return base64.b64encode(box.encrypt(data)).decode()


def unseal(box: Box, b64: str) -> dict:
    data = box.decrypt(base64.b64decode(b64))
    return json.loads(data)
