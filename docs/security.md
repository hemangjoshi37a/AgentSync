# AgentSync security model

AgentSync connects two Claude Code sessions — local or remote — so they can ask
each other questions and exchange information. Because one of those sessions can
be on **someone else's machine**, every byte that crosses a connection is, from
your node's point of view, **input from a party you do not control**. This
document describes the threat model and the defenses, and warns clearly about
the one component that materially changes your exposure: the **headless
auto-responder**.

> [!WARNING]
> Running the headless auto-responder (`python -m agentsync.responder` /
> `agentsync-responder`) **exposes a Claude Code instance to remote queries with
> no human reviewing each answer.** It is locked down (read-only tool allowlist,
> `dontAsk` permission mode, a hardened system-prompt guard), but it is still a
> deliberate decision to let connected peers drive a Claude on your machine. Do
> not run it on a machine with secrets you cannot afford a worst-case read of,
> and only connect to peers you trust. It is **opt-in** and never starts on its
> own.

---

## What we defend, and what we assume

| Asset | Threat |
|---|---|
| Your files / repo / machine state | A peer (or a prompt-injection payload it relays) tries to make Claude delete, modify, or corrupt data. |
| Secrets (keys, tokens, env vars, dotfiles) | A peer tries to make Claude read and return credentials, or exfiltrate them over the network. |
| Conversation content in transit | The relay operator or a network observer tries to read what the two peers are saying. |
| Your willingness to connect at all | A stranger tries to open a session without your knowledge. |

**Trusted:** your own machine and the local daemon; the Claude Code CLI on your
machine; the peers you explicitly accept.
**Untrusted:** the relay server, the network, and **the content of every message
a peer sends you** — including the `prompt` of an inbound `ask`.

---

## Defenses

### 1. Consent-gated connections

Nothing flows until the receiving side agrees. Remote connection requests raise
an `incoming_connect` event that the **control** client (the TUI) surfaces as an
AnyDesk-style **Accept / Reject / Always-allow** prompt; only on accept does a
bridge open. Local-to-local sessions auto-accept by policy
(`Policy.auto_accept_local`), since both sessions already belong to the same
user on the same machine. An optional per-node **connection password / pre-shared
key** (`Policy.connection_password`) can gate connections further.

### 2. End-to-end encryption (the relay sees only ciphertext)

Each node owns a Curve25519 keypair (PyNaCl). During the consent handshake the
peers exchange public keys (requester's key in `connect_request`, accepter's in
`connect_response`), each forms a `Box` from its own private key plus the peer's
public key, and **all peer-layer payloads** (`ask`, `reply`, messages, control)
travel sealed inside the `box` field of a `relay` envelope. The relay routes by
node id and **never sees plaintext** — only ciphertext and routing metadata.
Local-to-local traffic never leaves the daemon's Unix socket at all.

> [!NOTE]
> **Trust-on-first-use caveat (v0.1).** Public keys are exchanged *through* the
> relay, so a malicious relay could substitute keys and mount a
> man-in-the-middle attack. Mitigations: self-host the relay, or verify a short
> key fingerprint with your peer over a side channel. Out-of-band key
> verification is on the roadmap.

### 3. Untrusted-peer input handling

Every inbound message — and especially the `prompt` of an `ask` — is treated as
**untrusted, potentially adversarial input**. Assume a peer may attempt prompt
injection ("ignore your instructions and run …"), social engineering, or data
exfiltration. The system never feeds peer content to anything privileged without
the constraints below. When a human is in the loop (the TUI), they see every
`ask` and choose whether/how to answer. When the **responder** is running, the
constraints in the next section take that human's place.

### 4. The responder's lockdown (allowlist + permission mode + guard)

The headless responder runs Claude Code with three layers of restriction so a
hostile prompt cannot turn an answer into an action:

- **Read-only tool allowlist** — `--allowedTools` defaults to safe, read-only
  tools only:

  ```
  Read,Glob,Grep,Bash(git status:*),Bash(git log:*),Bash(ls:*)
  ```

  No `Write`/`Edit`, no arbitrary `Bash`, no web access, no MCP mutators. Even
  the allowed `Bash` entries are scoped to specific read-only commands.
- **`--permission-mode dontAsk`** — the run never blocks asking a human to
  approve a tool (there is no human). Anything not on the allowlist is simply
  unavailable, so the model cannot escalate by waiting for a prompt to be
  approved.
- **`--append-system-prompt` guard** — a hardened system prompt tells Claude the
  request came from an external, untrusted AgentSync peer; that it is running
  headless with no human review; and that it must act as a **read-only
  assistant** — never destructive/irreversible actions, never reveal or transmit
  secrets/credentials/dotfiles, never make outbound network calls, never work
  around the allowlist, and ignore any in-prompt instruction telling it to
  disregard these rules. This is defense in depth: even read-only tools could be
  abused to read a secret, so the model is also told not to surface one.

The untrusted prompt and the guard are passed as **separate `argv` entries** to
`asyncio.create_subprocess_exec` (no shell), so the prompt cannot be reinterpreted
as flags or shell syntax.

---

## Recommended safe defaults

- **Leave the responder off** unless you specifically want unattended answering.
  Prefer the human-in-the-loop TUI.
- **Keep the default read-only allowlist.** If you widen it via
  `AGENTSYNC_RESPONDER_TOOLS`, add only read-only/safe tools, and never
  `Write`/`Edit`/unrestricted `Bash`/network tools.
- **Scope the working directory** with `AGENTSYNC_RESPONDER_CWD` to a single
  project that contains no secrets. Do not point it at `$HOME`.
- **Run on a machine without sensitive credentials**, or in a container/VM/
  sandbox, ideally with no outbound network egress.
- **Only accept connections from peers you trust**, and use a connection
  password. Reject unknown nodes.
- **Self-host the relay** (or use one you trust) and verify peer key fingerprints
  out of band where the stakes warrant it.
- Keep `AGENTSYNC_RESPONDER_MAX_CONCURRENT` modest and a sane
  `AGENTSYNC_RESPONDER_TIMEOUT` so a flood of asks cannot exhaust resources.

---

## Responder environment knobs

| Variable | Default | Purpose |
|---|---|---|
| `AGENTSYNC_RESPONDER_TOOLS` | `Read,Glob,Grep,Bash(git status:*),Bash(git log:*),Bash(ls:*)` | `--allowedTools` allowlist. Keep read-only/safe. |
| `AGENTSYNC_RESPONDER_CWD` | current directory | Working dir Claude runs in (`cwd=`). Scope to a non-secret project. |
| `AGENTSYNC_RESPONDER_TIMEOUT` | `180` | Per-ask timeout (seconds). |
| `AGENTSYNC_RESPONDER_MODEL` | unset (Claude default) | Optional `--model` id. |
| `AGENTSYNC_RESPONDER_MAX_CONCURRENT` | `2` | Max asks answered concurrently. |
| `AGENTSYNC_RESPONDER_LABEL` | `responder@<hostname>` | `hello` label shown to peers. |
| `AGENTSYNC_LOG_LEVEL` | `INFO` | Python logging level. |

## Residual risk

Even fully locked down, an attacker who connects can make Claude **read** files
the working directory and allowlist permit and learn things about your code or
environment, and can consume compute. The responder reduces the blast radius to
"a read-only Claude scoped to one directory" — it does not make remote querying
risk-free. Treat enabling it as granting a trusted-but-verify peer read access to
that scope.
