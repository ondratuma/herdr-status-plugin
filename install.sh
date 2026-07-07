#!/usr/bin/env bash
set -euo pipefail

# install.sh — symlink the scripts into ~/.local/bin and (re)link the herdr plugin.
# Idempotent and safe to re-run. The repo is the source of truth; ~/.local/bin holds links.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
PLUGIN_ID="ot.claude-status"

mkdir -p "$BIN_DIR"

# 1) symlink every script in bin/ onto PATH (overwrites any existing file/link)
for src in "$REPO"/bin/*; do
	name="$(basename "$src")"
	ln -sfn "$src" "$BIN_DIR/$name"
	chmod +x "$src"
	echo "linked  $BIN_DIR/$name -> $src"
done

# 2) render the plugin manifest from its template, injecting this repo's absolute bin path.
sed "s|__HERDR_STATUS_BIN__|${REPO}/bin/herdr-status|g" \
	"$REPO/plugin/herdr-plugin.toml.in" > "$REPO/plugin/herdr-plugin.toml"
echo "rendered  $REPO/plugin/herdr-plugin.toml"

# 3) (re)link the herdr plugin from this repo. Best-effort: needs a running herdr server.
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
