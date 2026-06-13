# AgentSync daemon ↔ client protocol (the integration contract)

This is the **single source of truth** for how anything talks to the local
AgentSync daemon. The TUI, the plugin's MCP server, the headless responder,
and the CLI are all **clients** of the daemon and speak this protocol.

## Transport

- The daemon listens on a **Unix-domain socket** at `agentsync.config.SOCKET_PATH`
  (default `~/.agentsync/daemon.sock`; the dir is overridable via the
  `AGENTSYNC_HOME` env var).
- Framing is **newline-delimited JSON**: each message is one compact JSON
  object followed by `\n`.
- Connect with `asyncio.open_unix_connection(str(SOCKET_PATH))`; write
  `json.dumps(obj).encode() + b"\n"`; read with `await reader.readline()`.
- A client's **first** message must be a `hello`.

## Useful library entry points

- `agentsync.config.SOCKET_PATH` — `pathlib.Path` to the socket.
- `agentsync.config.load_or_create() -> (Config, PrivateKey)` — `Config` has
  `.node_id`, `.label`, `.relay_url`, `.policy` (`Policy` has
  `.auto_accept_local`, `.require_consent_remote`, `.connection_password`).
- `agentsync.protocol` — wire constants and builders (peer layer).

## Addressing

- A **remote** peer is addressed by its **node id**, which always starts with
  `"AS-"` (e.g. `AS-7K3F-9210`).
- A **local** peer (another Claude session on the same machine) is addressed by
  its **session id**, a short token like `s1`, `s2` (does not start with `AS-`).

## Client → daemon commands (key: `cmd`)

| `cmd` | Fields | Meaning |
|---|---|---|
| `hello` | `label` (str), `role` (`"session"` \| `"control"`) | Register this client. **Must be first.** `session` = a Claude session that can answer asks; `control` = the TUI that receives consent prompts. |
| `peers` | — | Request the current peer list. |
| `connect` | `target` (node id or local session id) | Initiate a connection to a peer. |
| `ask` | `target`, `prompt` (str), `request_id` (unique str) | Ask a peer a question. The answer returns as a `reply` event with the same `request_id`. |
| `reply` | `request_id`, `body` (str), `ok` (bool) | Answer an inbound `ask` event that was delivered to this client. |
| `send` | `target`, `body` (str) | Fire-and-forget message to a peer. |
| `control` | `target`, `action` (`"pause"`\|`"resume"`\|`"stop"`) | Control an active bridge. |
| `accept` | `request_id` | Consent: accept an `incoming_connect` (sent by the TUI/control client). |
| `reject` | `request_id` | Consent: reject an `incoming_connect`. |
| `status` | — | Same payload as `peers`. |

## Daemon → client events (key: `event`)

| `event` | Fields | Meaning |
|---|---|---|
| `welcome` | `session_id`, `node_id`, `label` | Sent right after `hello`. |
| `peers` | `node_id`, `local` (list of `{session_id,label}`), `remote` (list of `{node_id,label,paused}`) | Current peers. Re-sent when membership changes. |
| `incoming_connect` | `request_id`, `from_node`, `from_label` | A remote node requests a connection. Sent to **control** clients; respond with `accept`/`reject`. |
| `connecting` | `peer`, `request_id` | Your outbound `connect` was sent; awaiting consent. |
| `connect_result` | `ok` (bool), `peer`, `reason` (str) | Outcome of a `connect`. |
| `connected` | `peer`, `label` | A bridge is now active with `peer`. |
| `ask` | `request_id`, `from`, `from_label`, `prompt` | A peer is asking you. Produce an answer via a `reply` command with the same `request_id`. |
| `reply` | `request_id`, `ok` (bool), `body` | The answer to an `ask` you sent. |
| `message` | `from`, `from_label`, `body` | A fire-and-forget message arrived. |
| `control` | `from`, `action` | A peer sent a pause/resume/stop. |
| `peer_gone` | `peer` | A peer disconnected. |
| `error` | `message` | Something went wrong (bad target, relay offline, etc.). |

## Notes for client authors

- Always read events in a loop; the daemon may push `peers`, `incoming_connect`,
  `ask`, `message`, `control`, and `peer_gone` at any time — not just in
  response to a command.
- For request/response (`ask`), generate a unique `request_id` (e.g.
  `uuid.uuid4().hex`) and match the returning `reply` event by it.
- Local connections auto-accept by policy (`auto_accept_local`); you generally
  do not need to `connect` before `ask`-ing a local session — addressing it by
  session id is enough. Remote peers require `connect` + consent first.
