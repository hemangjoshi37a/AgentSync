---
description: Toggle autonomous auto-answer mode (sessions answer peer asks with no human).
argument-hint: "on | off | status"
allowed-tools: Bash(touch:*), Bash(rm:*), Bash(ls:*), Bash(test:*), Bash(mkdir:*)
---

Toggle **AgentSync auto-respond mode** for this machine. When ON, every session's
own MCP server answers inbound `agentsync_ask` requests autonomously by running a
locked-down, **read-only** `claude -p` — no human needs to reply, and no extra
session entry is created.

The switch is the flag file `~/.agentsync/auto_respond.on` (and `auto_respond.off`
to force-disable). It is read at ask-time, so changes take effect immediately for
sessions already running the current plugin code — **no restart needed**.

The requested action is: **$ARGUMENTS**

Do the following based on that action (default to `status` if empty):

- **on** — run `mkdir -p ~/.agentsync && rm -f ~/.agentsync/auto_respond.off && touch ~/.agentsync/auto_respond.on`, then confirm auto-respond is ENABLED. Remind the user that incoming asks are treated as untrusted and Claude is locked to a read-only tool allowlist (`Read, Glob, Grep, git status/log, ls`); broaden it only via `AGENTSYNC_RESPONDER_TOOLS`.
- **off** — run `rm -f ~/.agentsync/auto_respond.on && touch ~/.agentsync/auto_respond.off`, then confirm auto-respond is DISABLED (asks will queue in the inbox for a human to answer with `agentsync_respond`).
- **status** — run `ls -1 ~/.agentsync/auto_respond.on ~/.agentsync/auto_respond.off 2>/dev/null` and report whether auto-respond is currently ON or OFF (on-flag wins; if neither exists it follows the `AGENTSYNC_AUTO_RESPOND` env default, which is OFF).

Keep the response to 1-2 lines.
