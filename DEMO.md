# Status demo — screenshot script

Paste this whole file to a **fresh agent running inside a herdr pane**. It walks that
pane's sidebar status through every icon, one at a time, and pauses so you can screenshot
each one. Drive it by typing `next` between rounds.

---

## Instructions to the agent

You are running inside a herdr pane. Your job: drive **this pane's** sidebar status through
each `herdr-status` icon so the user can screenshot it. The `herdr-status` helper is on PATH;
it is a no-op outside herdr.

**The one thing you must understand — there are two display modes:**

- **ACTIVE icons (⚡ working, 🔁 looping)** only render while the pane is **busy**. Hold them
  by setting the status and then running a `sleep` **in the same turn / same command**. Do
  **not** end your turn to show them — the instant you stop, herdr flips the pane to idle and
  the icon becomes 💤.
- **STOPPED icons (⏳ waiting, ✋ input, ✅ done, 💤 idle)** only render while the pane is
  **idle**. Show them by setting the status and **ending your turn**. The icon appears the
  moment you stop talking. Do **not** sleep or run anything after — that would make the pane
  busy and show ⚡ instead.
- **💀 stale** needs a 24h+ timer, which only happens by faking the state file's timestamp. It
  overrides every state, so hold it busy like an active icon.

**Pacing:** one status per round. After each round, **stop and wait for the user** to type
`next` (advance) or `again` (re-hold the same one). Never batch rounds — a single busy turn can
only ever show the active icons, so each stopped icon must be its own end-of-turn.

The label format is `<icon> <timer>`, and a self-reported detail rides after it
(e.g. `⚡ 1m fixing the parser`). Timers on the HOLD icons start at `0m` and climb during the
sleep; timers on the END-TURN icons start at `0m` when the pane goes idle and climb on their
own — wait a couple minutes if you want a bigger number in the shot.

### Preflight

```bash
echo "HERDR_ENV=${HERDR_ENV:-unset}  PANE=${HERDR_PANE_ID:-unset}"
```

If `HERDR_ENV` is not `1`, stop and tell the user to run this inside a herdr pane.

### Round 1 — ⚡ working  `[HOLD]`

One command (sets the status, then holds the pane busy for ~2 min so ⚡ stays up):

```bash
herdr-status working "fixing the parser" && \
echo "📸 Sidebar shows ⚡ now — shoot it. Holding ~2 min…" && sleep 120
```

Then end your turn: *"Held ⚡ working. Got the shot? Say `next` for 🔁, or `again` to re-hold."*

### Round 2 — 🔁 looping  `[HOLD]`

```bash
herdr-status looping "watching CI" && \
echo "📸 Sidebar shows 🔁 now — shoot it. Holding ~2 min…" && sleep 120
```

End turn: *"Held 🔁 looping. Say `next` for ⏳, or `again`."*

### Round 3 — ⏳ waiting  `[END TURN]`

```bash
herdr-status waiting "CI: integration suite"
```

Then **end your turn immediately** (no sleep, no more commands):
*"Sidebar now shows ⏳ and it stays put — shoot it whenever. Say `next` for ✋."*

### Round 4 — ✋ input  `[END TURN]`

```bash
herdr-status input "need your call on the migration plan"
```

End turn: *"Sidebar shows ✋. Shoot anytime. Say `next` for ✅."*

### Round 5 — ✅ done  `[END TURN]`

```bash
herdr-status done
```

End turn: *"Sidebar shows ✅. Shoot anytime. Say `next` for 💤."*

### Round 6 — 💤 idle  `[END TURN]`

```bash
herdr-status clear
```

`clear` drops the pane back to plain auto-tracking; when you go idle it shows 💤 (the default).
End turn: *"Sidebar shows 💤 (idle). Shoot anytime. Say `next` for 💀."*

### Round 7 — 💀 stale  `[HOLD + fake timestamp]`

A pane only goes stale after 24h. Fake the timer by back-dating this pane's state file (≈36h),
push it, then hold the pane busy so the skull stays up:

```bash
sess=default
case "${HERDR_SOCKET_PATH:-}" in
  "$HOME/.config/herdr/sessions/"*/herdr.sock)
    sess="${HERDR_SOCKET_PATH#"$HOME"/.config/herdr/sessions/}"; sess="${sess%/herdr.sock}" ;;
esac
sf="$HOME/.config/herdr/claude-status/$sess/${HERDR_PANE_ID//[^A-Za-z0-9]/_}.state"
mkdir -p "$(dirname "$sf")"
printf 'auto\n\n%s\n0\n' "$(( $(date +%s) - 130000 ))" > "$sf"   # since = ~36h ago
printf 'working' > "${sf%.state}.last"                           # stop the daemon re-anchoring
herdr-status __run push "$HERDR_PANE_ID" && \
echo "📸 Sidebar shows 💀 36h now — shoot it. Holding ~2 min…" && sleep 120
```

End turn: *"Held 💀 stale. That's every icon. Say `again` to re-hold 💀, or `done` to clean up."*
(Ending the turn re-anchors the timer, so the skull reverts to 💤 — that's expected; you already
shot it during the hold.)

### Cleanup (on `done`)

```bash
herdr-status clear     # back to normal auto-tracking
# or:  herdr-status off   # stop the tracker for this session entirely (panes fade in ~3 min)
```

---

## Optional bonus shots

- **Detail text** — any status takes a second arg shown after the timer:
  `herdr-status working "tailoring the resume"` → `⚡ 1m tailoring the resume`.
- **Rename a pane** — `herdr-status-rename "auth refactor"` relabels this pane in the sidebar
  (good for showing several panes in one repo telling them apart).
- **Cross-session board** — `herdr-status agents` (alias `ls`) prints every agent pane across
  *all* herdr sessions, longest-waiting first. Screenshot the **terminal output**, not the sidebar.
- **Exact timers** — to make a HOLD icon read a specific time (e.g. the blog's `⚡ 6m`), back-date
  `since` with the same trick as the stale step before holding: write the state file with
  `since = now - <seconds>`. Only works for the HOLD icons; the END-TURN icons re-anchor to `0m`
  when the pane goes idle.
