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
STATE_ROOT = os.path.expanduser("~/.config/herdr/claude-status")
HEARTBEAT = 30
STALE_S = 24 * 3600
TTL_MS = 180000
KEEPALIVE_S = 90
STOPPED_STATES = ("idle", "blocked", "done")
STOP_ICON = {"waiting": "⏳", "input": "✋", "done": "✅"}
IDLE_ICON = "💤"                   # default label for an idle pane (herdr's "idle" state)
STALE_ICON = "💀"                  # replaces the activity icon once a pane is stale (24h+)

def server_key(sock=None):
    # which herdr server this invocation talks to → its own state dir + daemon, so concurrent
    # sessions don't collide. Matches the names `herdr session list` reports.
    sock = sock or os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")
    home = os.path.expanduser("~")
    if sock == os.path.join(home, ".config/herdr/herdr.sock"):
        return "default"
    prefix = os.path.join(home, ".config/herdr/sessions") + os.sep
    suffix = os.sep + "herdr.sock"
    if sock.startswith(prefix) and sock.endswith(suffix):
        return sock[len(prefix):-len(suffix)] or "default"
    return "".join(c if c.isalnum() else "_" for c in sock)

STATE_DIR = os.path.join(STATE_ROOT, server_key())

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

def render(pane, status):
    # → (detail, icon, timer) for the pane's CURRENT agent_status. herdr ≥0.7.4 renders
    # custom pane metadata as $name tokens in the sidebar row layout, so the icon is
    # computed per push (events + the daemon heartbeat keep it current) instead of
    # pre-declaring one label per state.
    st = read_state(pane)
    if not st:
        return None
    intent, detail, since, _ = st
    elapsed = int(time.time()) - since
    timer = fmt_elapsed(elapsed)
    if elapsed >= STALE_S:                       # stale pane → the skull becomes the icon;
        icon = STALE_ICON                        # the activity state is moot
    elif status == "working":
        icon = "🔁" if intent == "looping" else "⚡"
    elif status in STOPPED_STATES:               # idle, blocked, done
        # ✅/⏳/✋ for a self-reported done/waiting/input; a plain idle pane shows 💤
        icon = STOP_ICON.get(intent, IDLE_ICON if status == "idle" else "")
    else:
        icon = ""
    return detail, icon, timer

def report(pane, *args):
    try:
        subprocess.run(["herdr", "pane", "report-metadata", pane, "--source", SOURCE,
                        "--seq", str(time.time_ns()), *args],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False)
    except Exception:
        pass

TOKENS = ("statusIcon", "name", "timeSinceLastAction", "custom_status")

# custom names keyed by AGENT SESSION id (e.g. the Claude Code session uuid), so a name
# survives herdr server restarts and follows the session into whatever pane hosts it.
# Global across herdr sessions on purpose — agent session ids are unique.
NAMES_PATH = os.path.join(STATE_ROOT, "names.json")

def load_names():
    try:
        with open(NAMES_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_names(names):
    try:
        os.makedirs(STATE_ROOT, exist_ok=True)
        tmp = NAMES_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(names, f, indent=1)
        os.replace(tmp, NAMES_PATH)
    except Exception:
        pass

def resolve_name(session, display):
    # → (name, stored): the session-keyed custom name when one exists, else what herdr
    # already shows (display_agent override or the detected agent)
    if session:
        stored = load_names().get(session)
        if stored:
            return stored, True
    return display, False

def push_pane(pane, status=None, session=None, display=None):
    if status is None:
        status, session, display = pane_info(pane)
    name, stored = resolve_name(session, display)
    r = render(pane, status)
    if not r:
        return
    detail, icon, timer = r
    # everything rides in metadata tokens the sidebar row layout references as
    # $statusIcon / $name (custom or detected name) / $timeSinceLastAction /
    # $custom_status (the self-reported detail)
    args = ["--ttl-ms", str(TTL_MS)]
    for token, value in zip(TOKENS, (icon, name, timer, detail)):
        args += ["--token", f"{token}={value}"] if value else ["--clear-token", token]
    if stored and name != display:
        # re-apply the custom name as display_agent (a restarted server forgot it),
        # so the agent palette and any `agent` row token show it too
        args += ["--display-agent", name]
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

def pane_fields(p):
    # (agent_status, agent session id, display name) from a pane JSON object
    session = (p.get("agent_session") or {}).get("value") or ""
    return (p.get("agent_status") or "working", session,
            p.get("display_agent") or p.get("agent") or "")

def pane_info(pane):
    try:
        d = json.loads(subprocess.check_output(["herdr", "pane", "get", pane], timeout=3))
        return pane_fields(d["result"]["pane"])
    except Exception:
        return ("working", "", "")

# ---- one-shot modes ----
if mode == "event":                                    # invoked by the herdr plugin per event
    try:
        data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")).get("data", {})
    except Exception:
        data = {}
    pane = data.get("pane_id")
    if pane:
        status, session, display = pane_info(pane)
        H = data.get("agent_status") or status
        now = int(time.time())
        reconcile(pane, H, now)
        push_pane(pane, H, session, display)
        ensure_daemon()
    sys.exit(0)
if mode == "push":
    if len(ARGS) > 1:
        push_pane(ARGS[1])
    sys.exit(0)
if mode == "setname":                                  # invoked by herdr-status-rename
    if len(ARGS) > 2:
        pane, new_name = ARGS[1], ARGS[2]
        _, session, _ = pane_info(pane)
        if session:                                    # persistent, follows the agent session
            names = load_names()
            names[session] = new_name
            save_names(names)
        else:                                          # no session id → pane-scoped fallback
            report(pane, "--display-agent", new_name)
        push_pane(pane)
    sys.exit(0)
if mode == "clearname":
    if len(ARGS) > 1:
        pane = ARGS[1]
        _, session, _ = pane_info(pane)
        if session:
            names = load_names()
            if names.pop(session, None) is not None:
                save_names(names)
        report(pane, "--clear-display-agent")
        push_pane(pane)
    sys.exit(0)
if mode == "clearpane":
    if len(ARGS) > 1:
        args = []
        for name in TOKENS:
            args += ["--clear-token", name]
        report(ARGS[1], *args, "--clear-state-labels")
    sys.exit(0)
if mode == "agents":
    # cross-session overview: every agent pane in every herdr session, with its current label.
    try:
        sessions = json.loads(subprocess.check_output(
            ["herdr", "session", "list", "--json"], timeout=3))["sessions"]
    except Exception:
        sessions = [{"name": "default",
                     "socket_path": os.path.expanduser("~/.config/herdr/herdr.sock")}]
    rows = []
    now = int(time.time())
    for s in sessions:
        name, sock = s.get("name", "?"), s.get("socket_path", "")
        env = dict(os.environ, HERDR_SOCKET_PATH=sock)
        try:
            panes = json.loads(subprocess.check_output(
                ["herdr", "pane", "list"], timeout=3, env=env))["result"]["panes"]
        except Exception:
            continue
        for p in panes:
            if not p.get("agent"):
                continue
            pid = p["pane_id"]
            status = p.get("agent_status") or "unknown"
            tokens = p.get("tokens") or {}
            label = f"{tokens.get('statusIcon', '')} {tokens.get('timeSinceLastAction', '')}".strip()
            # the sidebar NAME: herdr renders display_agent (what herdr-status-rename sets),
            # falling back to the detected agent when unnamed — NOT the pane label or cwd.
            disp = p.get("display_agent") or p.get("agent") or "—"
            detail, elapsed = "", -1                       # from this session's state file
            try:
                with open(os.path.join(STATE_ROOT, name, sanitize(pid) + ".state")) as f:
                    parts = f.read().split("\n")
                detail = parts[1].strip() if len(parts) > 1 else ""
                if len(parts) > 2 and parts[2].strip():
                    elapsed = now - int(parts[2])
            except Exception:
                pass
            rows.append({"sess": name, "pane": pid, "name": disp,
                         "state": status, "label": label, "detail": detail, "elapsed": elapsed})
    if not rows:
        print("no agent panes found in any herdr session")
        sys.exit(0)
    rows.sort(key=lambda r: r["elapsed"], reverse=True)    # longest time-since first
    cols = [("sess", "SESSION"), ("pane", "PANE"), ("name", "NAME"), ("state", "STATE")]
    width = {k: max(len(h), max(len(str(r[k])) for r in rows)) for k, h in cols}
    print("  ".join(h.ljust(width[k]) for k, h in cols) + "  STATUS")
    for r in rows:
        line = "  ".join(str(r[k]).ljust(width[k]) for k, _ in cols)
        line += "  " + (r["label"] or "—")
        if r["detail"]:
            line += f"  · {r['detail']}"
        print(line)
    sys.exit(0)
if mode != "daemon":
    sys.exit(0)

# ================= daemon: timer + register/prune =================
# herdr already reports each pane's agent_status (idle/working/blocked/…); the daemon just
# polls it on a tick to advance the timer and register/prune. State transitions also arrive
# promptly via the plugin's event hook (which runs `__run event`), so no subscription is needed.
last_push = {}

def list_agent_panes():
    # pane_id → (agent_status, session id, display name); one `pane list` covers the tick
    try:
        data = json.loads(subprocess.check_output(["herdr", "pane", "list"], timeout=3))
        return {p["pane_id"]: pane_fields(p)
                for p in data["result"]["panes"] if p.get("agent")}
    except Exception:
        return None

def maybe_push(pane, status, session, display, now):
    r = render(pane, status)
    if not r:
        return
    sig = (r, resolve_name(session, display))
    prev = last_push.get(pane)
    if prev is None or prev[0] != sig or now - prev[1] >= KEEPALIVE_S:
        push_pane(pane, status, session, display)
        last_push[pane] = (sig, now)

def resync(now):
    panes = list_agent_panes()
    if panes is None:
        return
    for pane, (H, session, display) in panes.items():
        reconcile(pane, H, now)
        maybe_push(pane, H, session, display, now)
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
