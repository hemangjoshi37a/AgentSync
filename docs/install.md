# Installing & using AgentSync

> ⚠️ **Alpha (v0.1).** AgentSync is early / work in progress — APIs, the wire
> protocol, and commands will change. See the [Status table in the
> README](../README.md#status) for what's built. Treat this as the intended
> setup flow.

AgentSync has three moving parts:

| Part | What it is | Where it runs |
|---|---|---|
| **`agentsync` daemon + CLI** | Per-machine local hub + relay gateway, plus the `agentsync` command and TUI | Once on each machine |
| **`agentsync-relay`** | A small WebSocket rendezvous server (only needed for **remote** connections) | Once, on a host both machines can dial out to |
| **AgentSync Claude Code plugin** | The MCP tools / hooks / slash commands a Claude session loads | Inside Claude Code |

Two sessions on the **same machine** don't need a relay at all — the daemon
routes them locally. You only need a relay to connect sessions on **different
machines**.

---

## 1. Install the daemon + CLI

AgentSync is not on PyPI yet, so install from source. (Once published, the
recommended path will be `pipx install agentsync`.)

```bash
git clone https://github.com/hemangjoshi37a/AgentSync.git
cd AgentSync
pipx install .          # isolated install, puts `agentsync` on your PATH
# or:  pip install .    # into the current (ideally virtual) environment
```

This installs three console commands (from `pyproject.toml`):

- `agentsync` — the node daemon, CLI, and TUI.
- `agentsync-relay` — the rendezvous server (only on the relay host).
- `agentsync-responder` — the optional headless auto-responder.

Requires **Python 3.11+**.

---

## 2. Same-machine use (no relay needed)

To bridge two Claude Code sessions on **one machine**, just start the node and
install the plugin (steps 4–5). The daemon routes local sessions to each other
over a Unix socket — no relay, no network.

```bash
agentsync up      # starts the daemon (if not already running) and opens the TUI
```

`agentsync up` prints this machine's stable **AgentSync ID** (e.g.
`AS-7K3F-9210`). Closing the TUI leaves the daemon running so you stay
reachable; use `agentsync stop` to shut it down.

---

## 3. Remote use — run a relay

For sessions on **different machines**, run one relay on a host both can reach
*outbound* (this is why it works through NAT/firewalls/one-way VPNs — both
nodes dial out; neither accepts inbound). The relay only routes opaque,
end-to-end-encrypted payloads; it never sees plaintext.

On the relay host (after installing the package):

```bash
agentsync-relay                                  # listens on 0.0.0.0:8787
agentsync-relay --host 0.0.0.0 --port 8787 -v    # explicit + verbose
```

> **Tip for a one-way-VPN setup** (you can reach machine B but not vice-versa):
> run the relay **on B** and point both nodes at it — B connects to its own
> `localhost` relay, and A connects out to B's address. No reverse connection
> needed.
>
> To expose a relay publicly, front it with a TLS-terminating reverse proxy
> (Caddy/nginx/Traefik) and have nodes connect over `wss://`.

Then, on **each** machine, set the relay and start the node:

```bash
agentsync set-relay wss://your-relay.example:8787   # or ws://<host>:8787 on a LAN
agentsync up
```

(You can also set the relay for a single run with the `AGENTSYNC_RELAY`
environment variable, which overrides the saved config.)

Identities and keys live under `~/.agentsync/` (git-ignored — never commit
them).

---

## 4. Install the Claude Code plugin

The plugin gives each Claude Code session the `agentsync_*` MCP tools, the
session-registration hooks, and the `/agentsync-*` slash commands. Install it
from the bundled marketplace. Inside Claude Code:

```text
/plugin marketplace add hemangjoshi37a/AgentSync
/plugin install agentsync
```

(`/plugin marketplace add` reads `.claude-plugin/marketplace.json` at the repo
root, which points at the plugin in the `./plugin` subdirectory.)

### Plugin requirement: the `mcp` Python package

The plugin's MCP server is launched by Claude Code as
`python3 ${CLAUDE_PLUGIN_ROOT}/mcp/server.py`. It is intentionally
self-contained and depends **only** on the
[`mcp`](https://pypi.org/project/mcp/) Python SDK plus the standard library, so
the `python3` Claude Code uses must have `mcp` importable:

```bash
python3 -c "import mcp"      # should succeed; if not:
pip install mcp
```

---

## 5. Use it

With the daemon up and the plugin installed, just ask a Claude Code session in
plain language, for example:

> "Connect to `AS-7K3F-9210` and ask them which file format their service expects."

The session uses the `agentsync_*` tools to request a connection; the other
side sees a **consent prompt** in its TUI and accepts (or rejects / always
allows). Once connected, either side can `agentsync_ask` the other, send
messages, and read replies — and a human can **pause / resume / stop /
disconnect** the bridge from the TUI at any time.

Useful slash commands inside a session: `/agentsync-peers` (list reachable
sessions), `/agentsync-connect <id>`, and `/agentsync-ask <id> <question>`.

### Optional: unattended auto-answering

To let a node answer peers' questions even with no human driving it, run the
headless responder (it uses a locked-down, read-only tool allowlist — read
[`security.md`](./security.md) first):

```bash
agentsync-responder
```

---

## CLI reference

```text
agentsync up               # ensure daemon is running, open the TUI console
agentsync daemon           # run the daemon in the foreground (service mode)
agentsync id               # print this node's id, label, relay, daemon status
agentsync peers            # list connectable peers (local + remote)
agentsync connect <id>     # connect to a peer by id
agentsync set-relay <url>  # set + persist the relay URL
agentsync trust <id>       # permanently trust a peer (auto-accept its connections)
agentsync untrust <id>     # revoke trust  (both accept --all to (un)trust every peer)
agentsync stop             # stop the running daemon
```

---

## Troubleshooting

- **`agentsync: command not found`** — the package isn't installed in the active
  environment. Re-run step 1, or run via the repo's virtualenv.
- **Plugin MCP server fails to start** — run `python3 -c "import mcp"` with the
  same `python3` Claude Code uses; install `mcp` if missing (step 4).
- **Nodes can't reach each other** — confirm both ran `agentsync set-relay` with
  the **same** URL, the relay is listening, and the port (or your proxied
  `wss://` endpoint) is reachable from both machines.
- **Same-machine peers not appearing** — local↔local routing is handled by the
  daemon, not a relay; make sure `agentsync up` is running for that user.
