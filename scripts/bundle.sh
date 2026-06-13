#!/usr/bin/env bash
# Re-sync the daemon runtime bundled inside the plugin from the source package.
#
# The Claude Code plugin must be self-contained (the marketplace only ships the
# `plugin/` directory), so the daemon's pure-stdlib modules are vendored into
# `plugin/runtime/agentsync/`. Run this after editing any of the bundled files
# so the installed plugin can auto-start the daemon with no `pip install`.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dst="$here/plugin/runtime/agentsync"
mkdir -p "$dst"
for f in __init__ protocol config crypto daemon statusline; do
  cp "$here/agentsync/$f.py" "$dst/$f.py"
done
echo "bundled agentsync runtime -> $dst"
