#!/usr/bin/env bash
set -euo pipefail

# install.sh — local-development installer for `herdr plugin link`.
# Symlinks the helper CLIs onto PATH and (re)links the herdr plugin from this repo.
# Idempotent and safe to re-run. The repo is the source of truth; ~/.local/bin holds links.
#
# For a normal install from GitHub you do NOT need this — `herdr plugin install <owner>/<repo>`
# clones the repo and runs the manifest's [[build]] step (scripts/install-bin.sh) for you.
# This script exists because build steps run on `plugin install` but NOT on `plugin link`, which
# is what local plugin authors use.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="ot.claude-status"

# 1) symlink the helper CLIs onto PATH (same step the [[build]] command runs on install)
"$REPO/scripts/install-bin.sh"

# 2) (re)link the herdr plugin from this repo. Best-effort: needs a running herdr server.
if command -v herdr >/dev/null 2>&1; then
	herdr plugin unlink "$PLUGIN_ID" >/dev/null 2>&1 || true
	if herdr plugin link "$REPO/plugin" >/dev/null 2>&1; then
		echo "linked  herdr plugin $PLUGIN_ID -> $REPO/plugin"
	else
		echo "note    'herdr plugin link $REPO/plugin' failed (herdr server not running?) — run it manually inside herdr" >&2
	fi
else
	echo "note    herdr not on PATH; skipped plugin link" >&2
fi

echo "done."
