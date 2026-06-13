---
description: Connect to an AgentSync peer by id. Usage: /agentsync-connect <peer-id>
allowed-tools: mcp__plugin_agentsync_bridge__agentsync_connect
---

Connect to the AgentSync peer identified by: `$ARGUMENTS`

Steps:

1. Treat `$ARGUMENTS` (equivalently `$1`) as the target peer id — a remote node
   id (starts with `AS-`, e.g. `AS-7K3F-9210`) or a local session id (e.g.
   `s1`). If no id was provided, ask the user for one and stop.
2. Call `mcp__plugin_agentsync_bridge__agentsync_connect` with that target.
3. Report the outcome. Remember that a **remote** peer must accept a consent
   prompt on its own machine before the bridge becomes active, so a successful
   call may mean "request sent, awaiting their consent" rather than "connected".
   Relay the tool's reason/status back to the user, and once connected let them
   know they can now use `/agentsync-ask` to talk to this peer.
