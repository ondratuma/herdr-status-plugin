#!/usr/bin/env bash
set -euo pipefail

# Symlink the helper CLIs (herdr-status, herdr-status-rename) into ~/.local/bin so agents
# and shells can call them by name. The plugin's own event hooks do NOT need this — they
# run bin/herdr-status via the manifest's relative path — but the agent self-report verbs
# (`herdr-status working`, `herdr-status-rename`) are invoked from arbitrary shells and so
# need to be on PATH.
#
# Runs in two places:
#   - as the plugin's `[[build]]` command during `herdr plugin install` (CWD = plugin root);
#   - from install.sh for local `herdr plugin link` development (build steps don't run on link).
# Resolve the repo from this script's own location so it works regardless of CWD.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"

mkdir -p "$BIN_DIR"
for name in herdr-status herdr-status-rename; do
	src="$REPO/bin/$name"
	chmod +x "$src"
	ln -sfn "$src" "$BIN_DIR/$name"
	echo "linked  $BIN_DIR/$name -> $src"
done

case ":${PATH}:" in
	*":${BIN_DIR}:"*) ;;
	*) echo "note    $BIN_DIR is not on your PATH — add it so the helper commands resolve" >&2 ;;
esac
