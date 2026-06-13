---
description: "Show/hide the AgentSync status line (node id + connected machines) in the Claude Code status bar."
allowed-tools: Read, Write, Edit, Bash(python3:*), Bash(ls:*)
---

Enable (default, or with `on`) or disable (`off`/`disable`) the **AgentSync status line**,
which shows this machine's AgentSync node id and currently-connected peers in the Claude
Code status bar. Requested mode: `$ARGUMENTS` (empty or `on` = enable; `off`/`disable` = disable).

The status bar is a single per-user setting, so this edits `~/.claude/settings.json`
carefully and preserves any status line the user already has.

## To ENABLE
1. Make sure the script exists at `~/.agentsync/statusline.py` (the plugin's SessionStart
   hook maintains it). If it is missing, locate the bundled copy with
   `ls ~/.claude/plugins/cache/*/agentsync/*/runtime/agentsync/statusline.py` and copy the
   newest match to `~/.agentsync/statusline.py`.
2. Read `~/.claude/settings.json` (treat a missing or empty file as `{}`). Keep all other keys.
3. If it already has a `statusLine` whose command does **not** contain `statusline.py`,
   first copy that value to a new key `statusLine_agentsync_backup` (so it can be restored).
4. Set `statusLine` to exactly:
   `{"type": "command", "command": "python3 <absolute path to ~/.agentsync/statusline.py>", "padding": 0}`
   (expand `~` to the real home directory).
5. Write valid JSON back to `~/.claude/settings.json`, then tell the user to open a new
   Claude Code session to see it.

## To DISABLE
If the current `statusLine` command contains `statusline.py`: restore
`statusLine_agentsync_backup` into `statusLine` if that key exists (and delete the backup
key), otherwise remove the `statusLine` key entirely. Save and confirm.

Always report exactly what you changed.
