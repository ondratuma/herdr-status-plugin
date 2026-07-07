# herdr_status_plugin

Per-pane activity status for [herdr](https://herdr.dev) agent panes — shows, in the sidebar,
what each agent pane is doing (icon) and for how long (a live timer), plus a helper to rename
the current pane.

## Parts

- **`bin/herdr-status`** — the system command + a singleton daemon (the implementation lives in
  `lib/herdr-status.py`). Agents self-report intent (`working` ⚡ / `looping` 🔁 / `waiting` ⏳ /
  `input` ✋ / `done` ✅, plus `clear` / `off`); the daemon advances the counting timer and
  registers/prunes panes. An idle pane shows 💤 by default (herdr reports the idle state).
- **`plugin/herdr-plugin.toml`** — the herdr plugin (`ot.claude-status`). Its `[[events]]` hooks
  on `pane.agent_detected` / `pane.agent_status_changed` run `herdr-status __run event`, so herdr
  captures the lifecycle events natively and pushes an immediate update on every transition.
- **`bin/herdr-status-rename`** — rename the current pane (sets both the pane label and the sidebar
  display name). `herdr-status-rename <name>` or `herdr-status-rename --clear`.

## Multiple herdr sessions

Each `herdr --session <name>` is an isolated server with its **own** config (plugins), socket, and
panes — so the plugin is **per-session**, not global. The default session is set up by `install.sh`;
for any other session, run **`herdr-status link`** from a pane inside it to register the plugin and
start that session's daemon. State and daemons are namespaced per server under
`~/.config/herdr/claude-status/<session>/`, so concurrent sessions never collide.

- **`herdr-status agents`** (alias `ls`) — list every agent pane across **all** herdr sessions,
  sorted by time-since (longest first). Columns: SESSION, PANE, NAME (the herdr **sidebar** name —
  `display_agent`, i.e. what `herdr-status-rename` sets; the detected agent when unnamed), STATE,
  and the live STATUS label (`💤 22h`, `⚡ 6m · <detail>`). Works from any terminal, in or out of herdr.

## Display model

Each label in herdr's `state_labels` slot is `<icon> <timer>` — herdr swaps the icon natively per
detected state: ⚡ working, 🔁 looping, 💤 idle, ✅/⏳/✋ on a self-report, and 💀 once a pane goes
stale (24h+) — where the skull becomes the icon and leads the actual hour count, e.g. `💀 36h`. The
self-reported detail (if any) rides in `custom_status`, so a working pane reads
`⚡ 6m fixing the parser` and an idle one just `💤 6m`.

## Install

```sh
./install.sh
```

Symlinks `bin/*` into `~/.local/bin`, renders `plugin/herdr-plugin.toml` from its template
(`plugin/herdr-plugin.toml.in`, injecting this repo's absolute path — the rendered file is
machine-specific and gitignored), and (re)links the plugin via `herdr plugin link`. Idempotent;
re-run after pulling changes. The plugin link step needs a running herdr server (run it from
inside herdr if it reports the server isn't reachable).

The agent-facing usage instructions live in `~/.claude-shared/CLAUDE.md` (the herdr session
status section), loaded into every Claude Code session.
