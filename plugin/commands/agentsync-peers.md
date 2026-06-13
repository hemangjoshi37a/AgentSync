---
description: List AgentSync peers (local + remote) you can talk to.
allowed-tools: mcp__plugin_agentsync_bridge__agentsync_peers
---

List the AgentSync peers this session can reach.

Call the `mcp__plugin_agentsync_bridge__agentsync_peers` tool, then present the
result clearly:

- **Local peers** — other Claude Code sessions on this same machine. These are
  addressed by their short **session id** (e.g. `s1`, `s2`) and, by policy,
  usually auto-accept, so they can be `agentsync_ask`-ed without a `connect`.
- **Remote peers** — sessions on other machines. These are addressed by their
  **node id** (always starts with `AS-`, e.g. `AS-7K3F-9210`) and require an
  `agentsync_connect` + the peer's consent before you can ask them anything.

For each peer show its id, its label, and whether it is currently connectable
(note any remote peer that is paused). If there are no peers, say so and remind
the user that the other side must be running its AgentSync node.
