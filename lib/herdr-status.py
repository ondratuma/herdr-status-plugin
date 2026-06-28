#!/usr/bin/env python3
"""herdr-status implementation: the __run one-shots (event / push / clearpane)
and the singleton daemon (timer + register/prune + live-prompt detection).

Invoked only by the `herdr-status` bash wrapper as
`python3 herdr-status.py <event|push PANE|clearpane PANE|daemon>` — not meant to
be run directly. See ../bin/herdr-status for the wrapper, the self-report verbs,
and the overall design notes.
"""

import json, os, select, socket, subprocess, sys, time

ARGS = sys.argv[1:]
mode = ARGS[0] if ARGS else ""

SOURCE = "claude-status"
STATE_DIR = os.path.expanduser("~/.config/herdr/claude-status")
SOCK_PATH = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")
HEARTBEAT = 30
STALE_S = 24 * 3600
TTL_MS = 180000
KEEPALIVE_S = 90
STOPPED_STATES = ("idle", "blocked", "done")
STOP_ICON = {"waiting": "⏳", "input": "✋", "done": "✅"}
PROMPT_ICON = ">_"                 # live prompt box detected → ready for input
PROMPT_RE = r"^\s*❯"              # claude's live_prompt_box line
PROMPT_SOURCE = "visible"          # match the live (visible) prompt box, not stale scrollback

def sanitize(pane): return "".join(c if c.isalnum() else "_" for c in pane)
def state_path(pane):  return os.path.join(STATE_DIR, sanitize(pane) + ".state")
def last_path(pane):   return os.path.join(STATE_DIR, sanitize(pane) + ".last")
def prompt_path(pane): return os.path.join(STATE_DIR, sanitize(pane) + ".prompt")

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

def has_prompt(pane):  return os.path.exists(prompt_path(pane))
def set_prompt(pane):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        open(prompt_path(pane), "w").close()
    except Exception:
        pass
def clear_prompt(pane):
    try: os.remove(prompt_path(pane))
    except Exception: pass

def fmt_elapsed(sec):
    if sec >= STALE_S:
        return "24h+ 💀"
    m = sec // 60
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60}m"

def render(pane):
    st = read_state(pane)
    if not st:
        return None
    intent, detail, since, _ = st
    timer = fmt_elapsed(int(time.time()) - since)
    working_lbl = "🔨"
    stopped_lbl = ""
    if intent == "working":
        working_lbl = f"🔨 {detail}".rstrip()
    elif intent == "looping":
        working_lbl = f"🔁 {detail}".rstrip()
    elif intent in STOP_ICON:
        ic = STOP_ICON[intent]
        stopped_lbl = f"{ic} {detail}".rstrip() if detail else ic
    labels = {"working": working_lbl, "unknown": ""}
    for s in STOPPED_STATES:
        labels[s] = stopped_lbl
    if intent not in STOP_ICON and has_prompt(pane):
        labels["idle"] = PROMPT_ICON           # prompt box up → idle = ready for input
    # lead every state label with the timer so the elapsed time is ALWAYS the first thing
    # shown, whichever state herdr swaps in (custom_status is no longer used for it)
    labels = {state: f"{timer} {rest}".rstrip() for state, rest in labels.items()}
    return timer, labels

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
    _timer, labels = r
    # the timer now leads each state label; clear custom_status so it isn't shown twice
    args = ["--clear-custom-status", "--ttl-ms", str(TTL_MS)]
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
        if H == "working":
            clear_prompt(pane)                         # prompt box is gone
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

# ================= daemon: timer + register/prune + live-prompt detection =================
last_push = {}
sub_set = set()
sock = None

def list_agent_panes():
    try:
        data = json.loads(subprocess.check_output(["herdr", "pane", "list"], timeout=3))
        return {p["pane_id"]: (p.get("agent_status") or "unknown")
                for p in data["result"]["panes"] if p.get("agent")}
    except Exception:
        return None

def connect():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK_PATH)
    subs = [{"type": "pane.output_matched", "pane_id": p, "source": PROMPT_SOURCE,
             "match": {"type": "regex", "value": PROMPT_RE}, "strip_ansi": True}
            for p in sub_set]
    if subs:
        s.sendall((json.dumps({"id": "sub", "method": "events.subscribe",
                               "params": {"subscriptions": subs}}) + "\n").encode())
    return s

def maybe_push(pane, now):
    r = render(pane)
    if not r:
        return
    timer, labels = r
    sig = (timer, tuple(sorted(labels.items())))
    prev = last_push.get(pane)
    if prev is None or prev[0] != sig or now - prev[1] >= KEEPALIVE_S:
        push_pane(pane)
        last_push[pane] = (sig, now)

def resync(now):
    global sock, sub_set
    panes = list_agent_panes()
    if panes is None:
        return
    if set(panes) != sub_set:                          # pane set changed → refresh prompt subs
        sub_set = set(panes)
        try:
            if sock is not None: sock.close()
        except Exception: pass
        try: sock = connect()
        except Exception: sock = None
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
                for ext in (".state", ".last", ".prompt"):
                    try: os.remove(os.path.join(STATE_DIR, base + ext))
                    except Exception: pass
                last_push.pop(base, None)

sub_set = set(list_agent_panes() or {})
try:
    sock = connect()
except Exception:
    sock = None
resync(int(time.time()))
last_resync = int(time.time())

while True:
    ready = []
    if sock is not None:
        try:
            ready, _, _ = select.select([sock], [], [], HEARTBEAT)
        except Exception:
            ready = []
            try: sock.close()
            except Exception: pass
            sock = None
    else:
        time.sleep(HEARTBEAT)
    now = int(time.time())
    if ready:
        try:
            chunk = sock.recv(65536)
        except Exception:
            chunk = b""
        if not chunk:                                  # stream closed → reconnect next loop
            try: sock.close()
            except Exception: pass
            sock = None
        else:
            for line in chunk.split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                pane = ev.get("data", {}).get("pane_id")
                if pane:                                # any pushed line = a prompt-box match
                    set_prompt(pane)
                    maybe_push(pane, now)
    if now - last_resync >= HEARTBEAT:                  # timer tick + register/prune
        resync(now)
        last_resync = now
