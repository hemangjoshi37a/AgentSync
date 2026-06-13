# AgentSync MCP server

The MCP server that gives a Claude Code session the `agentsync_*` tools —
"AnyDesk for Claude Code sessions". It is a thin, **self-contained** stdio
bridge between Claude Code and the local AgentSync daemon: it speaks the
newline-delimited JSON protocol documented in
[`docs/PROTOCOL.md`](../../docs/PROTOCOL.md) over the daemon's Unix socket and
re-exposes that bridge as MCP tools.

## How it is launched

Claude Code starts it as part of the AgentSync plugin:

```sh
python3 ${CLAUDE_PLUGIN_ROOT}/mcp/server.py
```

stdout is the MCP JSON-RPC channel; all logs go to **stderr**.

## Dependency

The **only** dependency is the official MCP Python SDK:

```sh
pip install mcp
```

(Plus the Python standard library; Python 3.11+.) This file intentionally does
**not** import the `agentsync` package — it ships inside the plugin and must run
in environments where only `mcp` is installed. The few protocol constants it
needs are inlined.

## Configuration (environment variables)

| Variable           | Purpose                                                                                   | Default                     |
|--------------------|-------------------------------------------------------------------------------------------|-----------------------------|
| `AGENTSYNC_SOCKET` | Explicit path to the daemon's Unix socket. Takes precedence over everything below.        | —                           |
| `AGENTSYNC_HOME`   | AgentSync home directory; the socket is `$AGENTSYNC_HOME/daemon.sock` when `..._SOCKET` is unset. | —                    |
| `AGENTSYNC_LABEL`  | Human-readable label for this session.                                                    | basename of the working dir |
| `AGENTSYNC_LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, …), to stderr.                                         | `INFO`                      |

Socket path resolution: `AGENTSYNC_SOCKET` → `$AGENTSYNC_HOME/daemon.sock` →
`~/.agentsync/daemon.sock`.

## Tools

| Tool | Description |
|------|-------------|
| `agentsync_whoami()` | Returns this session's `{node_id, session_id, label}`. |
| `agentsync_peers()` | Returns reachable peers: `{local: [...], remote: [...]}` (fresh snapshot). |
| `agentsync_connect(peer_id, timeout=30)` | Open a bridge to a peer (consent handshake for remote peers). Returns `{ok, peer, reason}`. |
| `agentsync_ask(peer, prompt, timeout=120)` | Ask a peer and await the reply. Returns `{ok, body}` (or `{ok: False, error: "timeout"}`). |
| `agentsync_send(peer, body)` | Fire-and-forget message to a peer. Returns `{ok: True}`. |
| `agentsync_inbox()` | Returns `{asks, messages}`: pending asks are **peeked**, messages are **drained**. |
| `agentsync_respond(request_id, answer, ok=True)` | Answer a pending inbound ask; removes it from the inbox. Returns `{ok: True}`. |
| `agentsync_control(peer, action)` | Control an active bridge; `action` ∈ `{pause, resume, stop}`. Returns `{ok: True}`. |

Peers are addressed by **`node_id`** (remote; always starts with `AS-`) or by
**`session_id`** (local; a short token like `s1`). See `docs/PROTOCOL.md` for
the full addressing and event model.

## Behaviour notes

- A single persistent connection to the daemon is established lazily on the
  first tool call and **reconnects automatically** (re-sending `hello`) if it
  drops.
- A background reader task continuously dispatches daemon events: `reply`
  events resolve the future for the matching `agentsync_ask`; inbound `ask`
  events are appended to the inbox; `message` events are buffered for
  `agentsync_inbox`; `connect_result` resolves the matching
  `agentsync_connect`; `peers`/`status` update the cached snapshot.
- Tools never raise: on failure they return `{"ok": False, "error": "..."}`.
