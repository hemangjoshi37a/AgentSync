# AgentSync MCP server

`server.py` gives a Claude Code session the `agentsync_*` tools and bridges them
to the local AgentSync daemon over a Unix-domain socket. It is a fully
MCP-compliant stdio server.

**Zero dependencies.** It is pure Python standard library — it speaks the MCP
stdio protocol (JSON-RPC 2.0, newline-delimited) *directly* and does **not**
require the `mcp` SDK or the `agentsync` package. The only requirement is
`python3` (3.11+), which Claude Code already provides, so **installing the
plugin is the only setup needed** — nothing to `pip install`.

Claude Code launches it as:

    python3 ${CLAUDE_PLUGIN_ROOT}/mcp/server.py

On first tool use it auto-starts the local daemon (from the bundled
`plugin/runtime/`) if it isn't already running — no `agentsync up` required.
The newline-delimited daemon protocol is documented in
[`docs/PROTOCOL.md`](../../docs/PROTOCOL.md).

## Tools
- `agentsync_whoami` — this session's node id / session id / label.
- `agentsync_peers` — list connectable peers (local + remote).
- `agentsync_connect(peer_id, timeout)` — connect to a peer (remote needs consent).
- `agentsync_ask(peer, prompt, timeout)` — ask a peer (or a **list** of peers) and get the answer(s).
- `agentsync_send(to, body, cc, bcc)` — selective message, email-style To/CC/BCC.
- `agentsync_broadcast(body, exclude)` — message all connected peers.
- `agentsync_inbox()` — pending asks + messages delivered to this session.
- `agentsync_respond(request_id, answer, ok)` — answer an inbound ask.
- `agentsync_control(peer, action)` — pause / resume / stop a bridge.

## Environment
- `AGENTSYNC_SOCKET` — daemon socket path override.
- `AGENTSYNC_HOME` — config/state dir (default `~/.agentsync`).
- `AGENTSYNC_LABEL` — this session's label (default: cwd basename).
- `AGENTSYNC_LOG_LEVEL` — stderr log level (default `INFO`).
