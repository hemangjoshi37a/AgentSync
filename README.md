<!--
AgentSync — AnyDesk for Claude Code sessions.
Connect any two Claude Code (Anthropic) sessions — local or remote — and let them
ask each other questions. Claude Code plugin + MCP server for multi-agent,
agent-to-agent communication with consent, end-to-end encryption, and human-in-the-loop control.
Keywords: Claude Code, MCP server, Claude Code plugin, multi-agent, agent-to-agent,
AI agent communication, session bridging, remote AI pair programming, NAT traversal, Anthropic.
-->

<div align="center" id="top">

<img src="docs/assets/agentsync-hero.jpg" alt="AgentSync — AnyDesk for Claude Code sessions: connect, ask, and sync any two Claude Code AI agents locally or across the internet" width="840">

<h1>AgentSync</h1>

<h3>🔗 AnyDesk for Claude Code sessions</h3>

<p><b>Connect any two Claude&nbsp;Code sessions — on one machine or across the internet — and let them ask each other questions.</b><br>
A consent handshake to pair, end-to-end encryption on the wire, and a human in the loop the whole time: accept, pause, resume, or stop on demand.</p>

<p>
<a href="https://github.com/hemangjoshi37a/AgentSync/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/hemangjoshi37a/AgentSync?style=for-the-badge&color=6E56CF&label=release"></a>
<a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/github/license/hemangjoshi37a/AgentSync?style=for-the-badge&color=2EA043"></a>
<a href="https://github.com/hemangjoshi37a/AgentSync/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/hemangjoshi37a/AgentSync?style=for-the-badge&color=F5A623"></a>
</p>

<p>
<img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
<img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin%20%2B%20MCP-D97757?style=flat-square&logo=anthropic&logoColor=white">
<img alt="Zero dependencies" src="https://img.shields.io/badge/local%20deps-zero%20(stdlib)-2EA043?style=flat-square">
<img alt="End-to-end encrypted" src="https://img.shields.io/badge/wire-E2E%20encrypted-6E56CF?style=flat-square&logo=letsencrypt&logoColor=white">
<img alt="Platforms: Linux and macOS" src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS-555?style=flat-square&logo=linux&logoColor=white">
<a href="#-contributing"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-2EA043?style=flat-square"></a>
</p>

<p>
<a href="#-quickstart"><b>Quickstart</b></a> ·
<a href="#-features"><b>Features</b></a> ·
<a href="#-how-it-works"><b>How it works</b></a> ·
<a href="#-the-agentsync-toolbelt"><b>Tools</b></a> ·
<a href="#-security-model"><b>Security</b></a> ·
<a href="#-faq"><b>FAQ</b></a> ·
<a href="#-roadmap"><b>Roadmap</b></a>
</p>

</div>

---

**AgentSync** is an open-source [Claude&nbsp;Code](https://claude.com/claude-code) plugin (an [MCP](https://modelcontextprotocol.io) server) that turns isolated AI coding sessions into a **collaborating mesh**. Inspired by AnyDesk and TeamViewer, it lets one Claude&nbsp;Code session **request a connection** to another by a short ID, the other side **accepts a consent prompt**, and from then on the two agents can **ask each other questions, exchange messages, and pass results back and forth** — bidirectionally, with a human able to intervene at any moment.

It works the same whether the sessions sit in two terminals on your laptop **or** on two machines on opposite sides of a corporate firewall, NAT, or VPN — because, like AnyDesk, **both ends only dial *outbound*** to a rendezvous relay.

> [!TIP]
> **New in v0.1.1 — autonomous answering.** A session can now answer peer questions *on its own* by running a locked-down, read-only `claude -p` — no human needed on the other side. Toggle it live with `/agentsync-auto on`. [Jump to it ↓](#-autonomous-mode)

<div align="center">
<br>
⭐ <b>If AgentSync is useful to you, consider starring the repo — it genuinely helps.</b> ⭐
</div>

---

<details>
<summary><b>📖 Table of contents</b></summary>

- [Why AgentSync?](#-why-agentsync)
- [Features](#-features)
- [See it in action](#-see-it-in-action)
- [Quickstart](#-quickstart)
- [How it works](#-how-it-works)
- [Usage](#-usage)
- [The agentsync toolbelt](#-the-agentsync-toolbelt)
- [Autonomous mode](#-autonomous-mode)
- [Security model](#-security-model)
- [Core concepts](#-core-concepts)
- [Project status](#-project-status)
- [How it compares](#-how-it-compares)
- [Roadmap](#-roadmap)
- [FAQ](#-faq)
- [Contributing](#-contributing)
- [License](#-license)

</details>

## 💡 Why AgentSync?

A single Claude&nbsp;Code session only knows its own repository. Real engineering, though, spans **many repos, machines, and contexts at once** — and copy-pasting between terminals to keep agents in sync is slow, lossy, and error-prone.

AgentSync lets specialized sessions **cooperate directly**:

- 🧩 A **frontend** session asks a **backend** session for the exact API contract — no guessing.
- 🏭 A **marketplace** repo asks a **machine-controller** repo to confirm a file format.
- 🖥️ An on-prem **GPU box** session answers questions from your **laptop** session, across a VPN.
- 🤖 A driver agent **fans a question out to several sessions at once** and collects every answer.

All with explicit human consent, end-to-end encryption, and instant pause/stop — and zero copy-pasting between windows.

## ✨ Features

| | Capability | What it means for you |
|:--:|---|---|
| 🤝 | **Consent handshake** | Nothing connects until the receiving side accepts (AnyDesk-style). Optionally "always allow" a peer. |
| 🏠 | **Local bridging, zero setup** | Two sessions on one machine reach each other instantly over a Unix socket — **no `pip install`, no config**. |
| 🌐 | **Remote bridging, NAT/VPN-proof** | Outbound-only WebSocket relay punches through firewalls, NAT, and VPNs without port-forwarding. |
| 🔐 | **End-to-end encryption** | Peers exchange keys on consent (PyNaCl); the relay only ever sees ciphertext + routing metadata. |
| 💬 | **Email-style messaging** | `To` / `CC` / `BCC` so only the relevant sessions receive a message — saving everyone else's tokens. |
| 🕸️ | **Many-to-many mesh** | One session can hold several peers at once and ask a **list** of them concurrently. |
| 🤖 | **Autonomous answering** | Opt-in: a session answers peers by itself via a locked-down, read-only `claude -p`. |
| ⏸️ | **Always interruptible** | Pause, resume, stop, or disconnect any bridge instantly — the human is always in control. |
| 🧰 | **Native to Claude Code** | Ships as MCP tools + slash commands + a live status line. Just talk to your session in plain English. |
| 🪶 | **Pure standard library** | The plugin and daemon are stdlib-only (PyNaCl/websockets are lazy-loaded for the remote path). |

## 🎬 See it in action

Once two sessions are paired, either one can simply be *asked* — in plain language — to query the other. With autonomous mode on, the other side answers **completely on its own**:

```text
You  ▸  "Ask the test session what files are in its working directory."

  Session A  ──agentsync_ask("s5", "run ls and list your files")──▶  Session B (s5)
                                                                        │
                                                  (no human — B's own MCP server
                                                   runs a read-only `claude -p`)
                                                                        │
  Session A  ◀──────────────── reply ─────────────────────────────────┘

A answers you:
  "s5's working dir is /home/you/test — it contains: README.md, src/,
   package.json, .github/, and 12 other entries."
```

No second human, no copy-paste, no shared scratch file — just one agent asking another and getting a real, read-from-disk answer.

## 🚀 Quickstart

### Local — two sessions on one machine (the 10-second path)

Inside any Claude&nbsp;Code session, run:

```text
/plugin marketplace add hemangjoshi37a/AgentSync
/plugin install agentsync@agentsync-marketplace
```

**That's the entire setup.** On session start the plugin **auto-starts a small background daemon** (pure standard library — no `pip install`, no `agentsync up`), so any two Claude&nbsp;Code sessions on this machine can immediately reach each other. Then just talk to your session:

> *"List my AgentSync peers, then ask the other session to summarize the file it's editing."*

…or use the slash commands `/agentsync-peers` and `/agentsync-ask <id> <question>`.

### Remote — two machines, across NAT / firewall / VPN

Remote pairing needs a **relay** both machines can dial out to (AnyDesk-style). Install the CLI from source, run a relay anywhere reachable, and point each node at it:

```bash
git clone https://github.com/hemangjoshi37a/AgentSync.git && cd AgentSync && pipx install .
agentsync-relay                              # on a host both machines can reach (:8787)
agentsync set-relay wss://your-relay:8787    # run on each node
```

Then, inside a session: *"connect to `AS-…-B` and ask which file format their service expects."* The other side consents once (or permanently with `agentsync trust <id>`), and the two agents talk.

<div align="center"><sub>📦 Requires Python 3.11+ for the CLI/relay. The local plugin needs only the <code>python3</code> Claude Code already runs.</sub></div>

## 🧩 How it works

```
   ┌─ Machine A (your laptop) ─────────────┐              ┌─ Machine B (GPU box, behind VPN) ─┐
   │                                        │              │                                    │
   │  Claude session  ──┐                   │              │              ┌──  Claude session   │
   │  Claude session  ──┤  Unix socket      │              │   Unix socket├──  Claude session   │
   │       (plugin/MCP) │                   │              │              │      (plugin/MCP)    │
   │                  ┌─▼──────────┐        │              │       ┌──────▼─────┐                │
   │                  │  agentsync │  outbound wss          outbound wss  agentsync │            │
   │                  │   daemon   │ ───────────────►  ◄─────────────── │   daemon  │            │
   │                  │ (local hub)│        │   ┌──────────────────┐   │(local hub) │            │
   │                  └────────────┘        │   │  agentsync-relay │   └────────────┘            │
   │   local↔local routed here, no network  │   │ (rendezvous,     │                            │
   └────────────────────────────────────────┘   │  E2E-encrypted)  │ ──────────────────────────┘
                                                 └──────────────────┘
              local ↔ local : stays inside the daemon (instant, no relay)
              local ↔ remote: daemon → relay → remote daemon
```

Four pieces, one repo:

| Component | What it is |
|---|---|
| **`agentsync` daemon** | Runs once per machine. The **local hub** (sessions connect over a Unix-domain socket) **and** the **relay gateway** (one outbound WebSocket to the relay). Routes by peer: local target → deliver over the socket; remote target → forward via the relay. Owns connection/consent state and pause/stop. A `flock` singleton guarantees exactly one daemon per machine. |
| **`agentsync` Claude Code plugin** | What a session loads: an **MCP server** exposing the `agentsync_*` tools, **hooks** that auto-register the session and auto-start the daemon, **slash commands**, and a **status line**. Pure stdlib — install = the only setup. |
| **`agentsync` TUI / CLI** | The AnyDesk-style face: shows this machine's ID, pops the **consent prompt**, displays the live cross-session transcript, and exposes **pause / resume / stop / disconnect**. |
| **`agentsync-relay`** | A small, self-hostable WebSocket rendezvous server. Both peers dial out to it (NAT/VPN-proof). It only **routes** — payloads are end-to-end encrypted, so the relay never sees plaintext. |

<details>
<summary><b>Connection flow (step by step)</b></summary>

**Remote (two machines):**
1. Both run `agentsync up` — each prints its ID and connects out to the relay.
2. On A, the session calls `agentsync_connect("AS-…-B")` (or you run `agentsync connect AS-…-B`).
3. B's TUI shows: *"`marketplace` on AS-…-A wants to connect — [A]ccept / [R]eject / [Always allow]"*.
4. On accept, an encrypted bridge opens. `agentsync_ask` / messages now flow both ways.
5. Either side can **pause / resume / stop** any time; the bridge tears down cleanly.

**Local (same PC):** no relay and no `agentsync up` — just install the plugin in each session. The daemon auto-starts and routes the two sessions directly over the Unix socket; local peers auto-accept by default (no prompt).

</details>

## 🧰 Usage

With the plugin installed, **just talk to your session in plain language** — *"list my AgentSync peers"*, *"ask `AS-7K3F-9210` which API version they're on"*, *"send the build plan to `s2` and CC `s3`"*. Claude picks the right tool. There are also slash commands and a CLI for driving it by hand.

**Slash commands**

| Command | What it does |
|---|---|
| `/agentsync-peers` | List the sessions you can reach (local + remote). |
| `/agentsync-connect <peer-id>` | Connect to a peer (remote peers must consent). |
| `/agentsync-ask <peer-id> <question>` | Ask a peer and report its answer. |
| `/agentsync-auto on\|off\|status` | Toggle autonomous auto-answer mode. |
| `/agentsync-statusline` | Show/hide the status line. |

**Status line** — the plugin shows your identity and live links right in the Claude&nbsp;Code status bar:

```text
🔗 AgentSync : Node = AS-7K3F-9210, Session = s8, Label = my-project, Local = s5·api-svc, Peers = gpu-box
```

So you (and Claude) always know **this session's id** and the **other sessions/machines you can address**. It's enabled automatically on first run if you don't already have a status line (one-time, never overrides yours).

<details>
<summary><b>CLI reference (installed from source — <code>pipx install .</code>)</b></summary>

```bash
agentsync up               # open the TUI console (the daemon auto-starts regardless)
agentsync id               # your node id, label, relay, daemon status
agentsync peers            # list peers
agentsync connect <id>     # connect to a peer
agentsync trust <id>       # permanently auto-accept a peer (untrust to revoke; --all for everyone)
agentsync set-relay <url>  # set the relay used for remote connections
agentsync stop             # stop the daemon
agentsync-relay            # run a rendezvous relay (self-hosted)
agentsync-responder        # run a dedicated headless answering node
```

</details>

## 📡 The agentsync toolbelt

The tools your model can call directly:

| Tool | Purpose |
|---|---|
| `agentsync_whoami` | Your node id, session id, and label. |
| `agentsync_peers` | List connectable peers (local sessions + remote nodes). |
| `agentsync_connect(peer_id)` | Open a bridge to a peer (consent for remote). |
| `agentsync_ask(peer, prompt)` | Ask one peer — **or a list of peers** — and get the answer(s). |
| `agentsync_send(to, body, cc, bcc)` | Selective message, email-style **To/CC/BCC** — only addressed sessions receive it. |
| `agentsync_broadcast(body)` | Message every connected peer at once. |
| `agentsync_inbox()` | Read questions/messages others sent you. |
| `agentsync_respond(request_id, answer)` | Answer a question from your inbox. |
| `agentsync_control(peer, action)` | `pause` / `resume` / `stop` a bridge. |

**Addressing:** local peers are addressed by **session id** (`s1`, `s2`, …) and auto-accept; remote peers are addressed by **node id** (`AS-XXXX-XXXX`) and need consent once (or `agentsync trust` to make it permanent).

## 🤖 Autonomous mode

By default, an inbound `agentsync_ask` waits in a session's inbox for a human to reply — ideal when a person is driving that side. To let a session answer **by itself**, flip on auto-respond:

```text
/agentsync-auto on        # this machine's sessions now auto-answer
/agentsync-auto off       # back to human-in-the-loop
/agentsync-auto status    # check current state
```

When ON, a session's **own MCP server** handles each inbound ask by running a locked-down, **read-only** `claude -p` and replying automatically — **no human, and no extra entry** in the peer list. The switch is the flag file `~/.agentsync/auto_respond.on`, read at ask-time (so it toggles live, **no restart**), and it is **OFF by default** for safety.

> [!WARNING]
> Incoming asks are treated as **untrusted input**. The headless answer runs with a strict **read-only tool allowlist** (`Read, Glob, Grep, git status/log, ls`) plus a guard system prompt — it cannot modify files or exfiltrate secrets. Broaden it only via `AGENTSYNC_RESPONDER_TOOLS`, and read [`docs/security.md`](docs/security.md) first. Be deliberate before leaving it on for **remote** peers.

For a **dedicated** answering node (a separate process, not tied to an interactive session), run the standalone responder instead:

```bash
agentsync-responder
```

## 🔐 Security model

A mesh where one agent can query another — and potentially trigger a headless Claude on the other machine — is powerful, and is treated as a **real attack surface**:

- **🔒 Consent-gated.** Nothing connects without the receiver accepting. Optional per-node connection password / pre-shared key.
- **📌 Persistent trust (opt-in).** Accept once with **"Always allow"** (or `agentsync trust <id>`) and the peer is saved to `trusted_nodes` — it auto-accepts across restarts. `agentsync untrust <id>` revokes; `agentsync trust --all` accepts everyone (use with care).
- **🛡️ End-to-end encrypted.** Peers exchange keys on consent (PyNaCl); the relay sees only ciphertext + routing metadata.
- **🚫 Untrusted by default.** All peer messages are treated as untrusted (prompt-injection aware). Auto-answered asks run under a tool allowlist + restrictive permission mode; destructive actions are denied by default.
- **⏹️ Always interruptible.** Pause/stop/disconnect are instant and human-controlled.
- **🙈 No secrets in the repo.** Identities/keys live under `~/.agentsync/` (git-ignored).

## 🧠 Core concepts

- **Node** — one machine running the daemon. Has a stable AgentSync ID like `AS-7K3F-9210`.
- **Session** — one Claude&nbsp;Code session connected to a node's daemon. Addressed by a short session id (`s1`, `s2`) locally, and by node id remotely.
- **Peer** — a session/node you're connected to. `agentsync_peers()` lists both the other Claude sessions on **your own machine** and the **remote** ones you've paired with.
- **Consent** — no traffic flows until the receiving side accepts. Remote always prompts; local same-user can auto-accept (configurable).

## 📊 Project status

> **v0.1.1 · alpha.** Local + remote bridging, consent, messaging, and autonomous answering all work and are tested with real Claude&nbsp;Code sessions. APIs may still change.

| Area | State |
|---|---|
| Architecture & design | ✅ done |
| Core protocol + E2E crypto (PyNaCl) | ✅ done |
| Relay rendezvous server | ✅ done · tested |
| Node daemon (local hub + relay gateway) | ✅ done · tested |
| TUI (consent + pause/resume/stop) | ✅ done |
| CLI (`up / peers / connect / set-relay / stop`) | ✅ done |
| Claude Code plugin (MCP tools + hooks + slash commands) | ✅ done |
| Email-style To/CC/BCC + many-to-many mesh | ✅ done |
| Autonomous auto-answer (folded into the MCP server) | ✅ done · tested |
| Persistent trusted-peer consent (`agentsync trust`) | ✅ done |
| Zero-setup install (stdlib daemon auto-starts — no `pip install`) | ✅ done |
| Battle-tested — stress suite + real Claude Code sessions | ✅ passing |
| Published to GitHub | ✅ live |
| Published to PyPI | ⏳ pending |

## 🆚 How it compares

|  | Copy-paste between terminals | Shared file / scratchpad | Plain MCP server | **AgentSync** |
|---|:--:|:--:|:--:|:--:|
| Two agents talk **directly** | ⚠️ manual | ⚠️ polling | ❌ agent↔tool only | ✅ |
| Works **across machines / NAT / VPN** | ❌ | ❌ | ❌ | ✅ outbound-only relay |
| **Consent** + human-in-the-loop | — | ❌ | ❌ | ✅ |
| **End-to-end encrypted** | — | ❌ | — | ✅ |
| **Autonomous** answering | ❌ | ❌ | ❌ | ✅ opt-in |
| **Zero install** (local) | ✅ | ✅ | varies | ✅ stdlib |

## 🧭 Roadmap

- **v0.1** — local↔local and local↔remote messaging + `ask`/reply, consent, pause/stop. ✅
- **v0.1.1** — autonomous auto-answer folded into the MCP server; cleaner session ids. ✅
- **v0.2** — approval policies for autonomous answers; richer multi-peer ergonomics.
- **v0.3** — group/broadcast rooms; richer TUI (transcript history, per-peer policies).
- **v0.4** — hosted public relay (opt-in), direct P2P with relay fallback (ICE-style).
- **Later** — capability/identity directory, audit logs, signed messages, PyPI release.

## ❓ FAQ

<details>
<summary><b>Does AgentSync work through a firewall, NAT, or corporate VPN?</b></summary>

Yes. Like AnyDesk, **both ends only dial *outbound*** to a rendezvous relay over WebSocket, so there's no port-forwarding and no inbound holes. As long as each machine can reach the relay, they can pair.
</details>

<details>
<summary><b>Do I need to <code>pip install</code> anything for local use?</b></summary>

No. The plugin and its auto-started daemon are **pure Python standard library**. Installing the plugin is the only step. `pip`/`pipx` is only needed for the source CLI, the self-hosted relay, or the standalone responder.
</details>

<details>
<summary><b>Can a remote peer run arbitrary commands on my machine?</b></summary>

No. Connections are consent-gated, and autonomous answers run a **read-only** `claude -p` locked to a safe tool allowlist (`Read, Glob, Grep, git status/log, ls`) with a guard prompt — no writes, no secret exfiltration, no network. Auto-answer is **off by default**. See the [security model](#-security-model).
</details>

<details>
<summary><b>Can more than two sessions talk at once?</b></summary>

Yes. A session can hold many peers simultaneously (a mesh), send email-style **To/CC/BCC** messages so only the right sessions are involved, and ask a **list** of peers concurrently with one call.
</details>

<details>
<summary><b>How is this different from a normal MCP server?</b></summary>

A normal MCP server connects **one agent to tools**. AgentSync *is* delivered as an MCP server, but it connects **agents to each other** — turning many isolated Claude&nbsp;Code sessions into a collaborating network, locally or across the internet.
</details>

<details>
<summary><b>Which platforms are supported?</b></summary>

Linux and macOS (the local hub uses Unix-domain sockets; the status line's spare-process detection is Linux-specific). Windows support is not a current target.
</details>

<details>
<summary><b>What model does autonomous mode use to answer?</b></summary>

Whatever `claude -p` resolves to by default, or set `AGENTSYNC_RESPONDER_MODEL`. Concurrency, timeout, and the tool allowlist are all environment-tunable.
</details>

## 🤝 Contributing

AgentSync is **MIT-licensed and built in the open**. Issues, ideas, and pull requests are very welcome. This is early-stage software — the protocol and APIs will change, so it's a great time to shape them.

- 🐛 [Open an issue](https://github.com/hemangjoshi37a/AgentSync/issues) for bugs or feature requests
- 🔧 Send a PR (the daemon↔client wire contract lives in [`docs/PROTOCOL.md`](docs/PROTOCOL.md))
- ⭐ Star the repo to help others discover it

## 📄 License

[MIT](./LICENSE) © 2026 [Hemang Joshi](https://github.com/hemangjoshi37a)

---

<div align="center">
<sub>Built for the <a href="https://claude.com/claude-code">Claude&nbsp;Code</a> ecosystem · <b>AnyDesk for AI coding agents</b> · made with 🔗 by <a href="https://github.com/hemangjoshi37a">@hemangjoshi37a</a></sub>
<br><br>
<a href="#top">⬆️ Back to top</a>
</div>
