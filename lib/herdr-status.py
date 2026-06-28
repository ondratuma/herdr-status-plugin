#!/usr/bin/env python3
"""herdr-status implementation: the __run one-shots (event / push / clearpane)
and the singleton daemon (timer + register/prune).

Invoked only by the `herdr-status` bash wrapper as
`python3 herdr-status.py <event|push PANE|clearpane PANE|daemon>` — not meant to
be run directly. See ../bin/herdr-status for the wrapper, the self-report verbs,
and the overall design notes.
"""

import json, os, subprocess, sys, time

ARGS = sys.argv[1:]
mode = ARGS[0] if ARGS else ""

SOURCE = "claude-status"
STATE_DIR = os.path.expanduser("~/.config/herdr/claude-status")
HEARTBEAT = 30
STALE_S = 24 * 3600
TTL_MS = 180000
KEEPALIVE_S = 90
STOPPED_STATES = ("idle", "blocked", "done")
STOP_ICON = {"waiting": "⏳", "input": "✋", "done": "✅"}
IDLE_ICON = "💤"                   # default label for an idle pane (herdr's "idle" state)
STALE_ICON = "💀"                  # replaces the activity icon once a pane is stale (24h+)

def sanitize(pane): return "".join(c if c.isalnum() else "_" for c in pane)
def state_path(pane):  return os.path.join(STATE_DIR, sanitize(pane) + ".state")
def last_path(pane):   return os.path.join(STATE_DIR, sanitize(pane) + ".last")

def read_state(pane):
    try:
        with open(state_path(pane)) as f:
            p = f.read().split("\n")
        return (p[0].strip() or "auto",
                p[1].strip() if len(p) > 1 else "",
                int(p[2]) if len(p) > 2 and p[2].strip() else int(time.time()),
                int(p[3]) if len(p) > 3 and p[3].strip() else 0)
    except Exception:
        return None

def write_state(pane, intent, detail, since, manual_ts):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = state_path(pane) + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{intent}\n{detail}\n{since}\n{manual_ts}\n")
    os.replace(tmp, state_path(pane))

def read_last(pane):
    try:
        return open(last_path(pane)).read().strip() or None
    except Exception:
        return None

def write_last(pane, H):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = last_path(pane) + ".tmp"
        with open(tmp, "w") as f:
            f.write(H)
        os.replace(tmp, last_path(pane))
    except Exception:
        pass

def fmt_elapsed(sec):
    if sec >= STALE_S:            # stale: whole hours (the 💀 is added as the icon in render)
        return f"{sec // 3600}h"
    m = sec // 60
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60}m"

def render(pane):
    st = read_state(pane)
    if not st:
        return None
    intent, detail, since, _ = st
    elapsed = int(time.time()) - since
    timer = fmt_elapsed(elapsed)
    # per-detected-state ICON only — the detail text rides in custom_status, not the label
    if elapsed >= STALE_S:                       # stale pane → the skull becomes the icon (and
        icons = {s: STALE_ICON for s in          # leads the time); the activity state is moot
                 ("working", "idle", "blocked", "done", "unknown")}
    else:
        work_icon = "🔁" if intent == "looping" else "⚡"
        stop_icon = STOP_ICON.get(intent, "")   # ✅/⏳/✋ for done/waiting/input, else ""
        icons = {"working": work_icon, "unknown": ""}
        for s in STOPPED_STATES:                 # idle, blocked, done
            icons[s] = stop_icon
        if intent not in STOP_ICON:
            icons["idle"] = IDLE_ICON           # idle pane → 💤
    # icon first, then the timer; a stateless pane (no icon) shows just the timer
    labels = {state: f"{icon} {timer}".strip() for state, icon in icons.items()}
    return detail, labels

def report(pane, *args):
    try:
        subprocess.run(["herdr", "pane", "report-metadata", pane, "--source", SOURCE,
                        "--seq", str(time.time_ns()), *args],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False)
    except Exception:
        pass

def push_pane(pane):
    r = render(pane)
    if not r:
        return
    detail, labels = r
    # the icon+timer live in the state label; the detail description rides in custom_status
    args = ["--ttl-ms", str(TTL_MS)]
    args += ["--custom-status", detail] if detail else ["--clear-custom-status"]
    for state, label in labels.items():
        args += ["--state-label", f"{state}={label}"]
    report(pane, *args)

# file-based so the one-shot event hook and the long-lived daemon agree
def reconcile(pane, H, now):
    st = read_state(pane)
    if st is None:
        write_state(pane, "auto", "", now, 0)
        write_last(pane, H)
        return
    intent, detail, since, manual_ts = st
    last = read_last(pane)
    if last is not None and last != H:                 # a real lifecycle transition
        since = now                                    # re-anchor the timer
        if manual_ts and intent in STOP_ICON and last != "working" and H == "working":
            intent, detail, manual_ts = "auto", "", 0  # waiting/input/done release on resume;
                                                       # working/looping persist
        write_state(pane, intent, detail, since, manual_ts)
    write_last(pane, H)

def ensure_daemon():
    pidf = os.path.join(STATE_DIR, "daemon.pid")
    try:
        os.kill(int(open(pidf).read().strip()), 0)
        return
    except Exception:
        pass
    binp = os.environ.get("HERDR_STATUS_BIN") or "herdr-status"
    try:
        subprocess.Popen([binp, "__ensure"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except Exception:
        pass

def pane_status(pane):
    try:
        d = json.loads(subprocess.check_output(["herdr", "pane", "get", pane], timeout=3))
        return d["result"]["pane"].get("agent_status") or "working"
    except Exception:
        return "working"

# ---- one-shot modes ----
if mode == "event":                                    # invoked by the herdr plugin per event
    try:
        data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")).get("data", {})
    except Exception:
        data = {}
    pane = data.get("pane_id")
    if pane:
        H = data.get("agent_status") or pane_status(pane)
        now = int(time.time())
        reconcile(pane, H, now)
        push_pane(pane)
        ensure_daemon()
    sys.exit(0)
if mode == "push":
    if len(ARGS) > 1:
        push_pane(ARGS[1])
    sys.exit(0)
if mode == "clearpane":
    if len(ARGS) > 1:
        report(ARGS[1], "--clear-custom-status", "--clear-state-labels")
    sys.exit(0)
if mode != "daemon":
    sys.exit(0)

# ================= daemon: timer + register/prune =================
# herdr already reports each pane's agent_status (idle/working/blocked/…); the daemon just
# polls it on a tick to advance the timer and register/prune. State transitions also arrive
# promptly via the plugin's event hook (which runs `__run event`), so no subscription is needed.
last_push = {}

def list_agent_panes():
    try:
        data = json.loads(subprocess.check_output(["herdr", "pane", "list"], timeout=3))
        return {p["pane_id"]: (p.get("agent_status") or "unknown")
                for p in data["result"]["panes"] if p.get("agent")}
    except Exception:
        return None

def maybe_push(pane, now):
    r = render(pane)
    if not r:
        return
    detail, labels = r
    sig = (detail, tuple(sorted(labels.items())))
    prev = last_push.get(pane)
    if prev is None or prev[0] != sig or now - prev[1] >= KEEPALIVE_S:
        push_pane(pane)
        last_push[pane] = (sig, now)

def resync(now):
    panes = list_agent_panes()
    if panes is None:
        return
    for pane, H in panes.items():
        reconcile(pane, H, now)
        maybe_push(pane, now)
    present = {sanitize(p) for p in panes}
    try:
        files = os.listdir(STATE_DIR)
    except Exception:
        files = []
    for f in files:
        if f.endswith(".state"):
            base = f[:-6]
            if base not in present:                     # pane gone → forget it
                for ext in (".state", ".last"):
                    try: os.remove(os.path.join(STATE_DIR, base + ext))
                    except Exception: pass
                last_push.pop(base, None)

while True:
    resync(int(time.time()))
    time.sleep(HEARTBEAT)
