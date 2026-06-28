# herdr_status_plugin

Per-pane activity status for [herdr](https://herdr.dev) agent panes — shows, in the sidebar,
what each agent pane is doing (icon) and for how long (a live timer), plus a helper to rename
the current pane.

## Parts

- **`bin/herdr-status`** — the system command + a singleton daemon. Agents self-report intent
  (`working` 🔨 / `looping` 🔁 / `waiting` ⏳ / `input` ✋ / `done` ✅, plus `clear` / `off`); the
  daemon advances the counting timer, registers/prunes panes, and subscribes to
  `pane.output_matched` to show `>_` when a pane is idle at its live prompt box.
- **`plugin/herdr-plugin.toml`** — the herdr plugin (`ot.claude-status`). Its `[[events]]` hooks
  on `pane.agent_detected` / `pane.agent_status_changed` run `herdr-status __run event`, so herdr
  captures the lifecycle events natively. (`pane.output_matched` is subscription-only, so the
  daemon — not the plugin — subscribes to it.)
- **`bin/herdr-status-rename`** — rename the current pane (sets both the pane label and the sidebar
  display name). `herdr-status-rename <name>` or `herdr-status-rename --clear`.

## Display model

Everything lives in herdr's `state_labels` slot (herdr swaps the label natively per detected
state). Every label is led by the counting timer, so the elapsed time is always the first thing
shown: a working pane reads `6m 🔨`, an idle pane at its prompt reads `6m >_`, a finished one
`6m ✅ <detail>`. Self-reports override the stopped-state icon (✋/⏳/✅). `custom_status` is unused.

## Install

```sh
./install.sh
```

Symlinks `bin/*` into `~/.local/bin` and (re)links the plugin from this repo via
`herdr plugin link`. Idempotent; re-run after pulling changes. The plugin link step needs a
running herdr server (run it from inside herdr if it reports the server isn't reachable).

The agent-facing usage instructions live in `~/.claude-shared/CLAUDE.md` (the herdr session
status section), loaded into every Claude Code session.
