"""AgentSync identity + configuration under ~/.agentsync/.

Layout:
    ~/.agentsync/
        config.toml     node id, label, relay url, policy
        node.key        Curve25519 private key (base64, mode 0600)
        daemon.sock      Unix-domain socket the local sessions connect to

The directory and key file are created on first run. Override the location
with the AGENTSYNC_HOME environment variable (useful for running two nodes
on one machine during testing).
"""

from __future__ import annotations

import os
import secrets
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import crypto

CONFIG_DIR = Path(os.environ.get("AGENTSYNC_HOME", Path.home() / ".agentsync"))
CONFIG_FILE = CONFIG_DIR / "config.toml"
KEY_FILE = CONFIG_DIR / "node.key"
SOCKET_PATH = CONFIG_DIR / "daemon.sock"
PIDFILE = CONFIG_DIR / "daemon.pid"
LOG_FILE = CONFIG_DIR / "daemon.log"
DEFAULT_RELAY = "ws://127.0.0.1:8787"


def _gen_node_id() -> str:
    return f"AS-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"


@dataclass
class Policy:
    auto_accept_local: bool = True
    require_consent_remote: bool = True
    connection_password: str = ""


@dataclass
class Config:
    node_id: str
    label: str
    relay_url: str = DEFAULT_RELAY
    policy: Policy = field(default_factory=Policy)


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _dump_toml(cfg: Config) -> str:
    p = cfg.policy
    return (
        f'node_id = "{_toml_escape(cfg.node_id)}"\n'
        f'label = "{_toml_escape(cfg.label)}"\n'
        f'relay_url = "{_toml_escape(cfg.relay_url)}"\n'
        f"\n[policy]\n"
        f"auto_accept_local = {str(p.auto_accept_local).lower()}\n"
        f"require_consent_remote = {str(p.require_consent_remote).lower()}\n"
        f'connection_password = "{_toml_escape(p.connection_password)}"\n'
    )


def ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def save(cfg: Config) -> None:
    ensure_dir()
    CONFIG_FILE.write_text(_dump_toml(cfg))
    os.chmod(CONFIG_FILE, 0o600)


def load_or_create() -> tuple[Config, crypto.PrivateKey]:
    """Load (or first-time create) this node's identity and config."""
    ensure_dir()

    # identity key
    if KEY_FILE.exists():
        priv = crypto.load_private_key(KEY_FILE.read_text().strip())
    else:
        priv = crypto.generate_private_key()
        KEY_FILE.write_text(crypto.dump_private_key(priv))
        os.chmod(KEY_FILE, 0o600)

    # config
    if CONFIG_FILE.exists():
        data = tomllib.loads(CONFIG_FILE.read_text())
        pol = data.get("policy", {})
        cfg = Config(
            node_id=data["node_id"],
            label=data.get("label", socket.gethostname()),
            relay_url=data.get("relay_url", DEFAULT_RELAY),
            policy=Policy(
                auto_accept_local=pol.get("auto_accept_local", True),
                require_consent_remote=pol.get("require_consent_remote", True),
                connection_password=pol.get("connection_password", ""),
            ),
        )
    else:
        cfg = Config(node_id=_gen_node_id(), label=socket.gethostname())
        save(cfg)

    # Runtime override for the relay endpoint (handy for tests / multi-node hosts).
    cfg.relay_url = os.environ.get("AGENTSYNC_RELAY", cfg.relay_url)
    return cfg, priv
