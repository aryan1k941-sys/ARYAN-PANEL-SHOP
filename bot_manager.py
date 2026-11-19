#!/usr/bin/env python3
"""
Aryan Bot Manager
-----------------
Website ke "My Telegram Bot" se jo user ne plan kharida hai, uska bot yahi chalata hai.
Har 10 second me Firebase `tg_bots` padhta hai:
  - plan active + desired=on + blocked nahi  -> FF_PANEL_SELLING_BOT_FILE.py ko alag process me start
  - plan expire / stop / block / delete      -> process band
  - settings (token/admin/shop...) badli     -> bot restart
Status Firebase me wapas likhta hai (running / error / invalid_token ...) jo user + admin panel me dikhta hai.

ENV:
  FIREBASE_DB_URL      (default: aryan-mods-4c084 realtime db)
  FIREBASE_DB_SECRET   Firebase Console > Project settings > Service accounts > Database secrets   (ya)
  FIREBASE_SA_JSON     service-account json ka path (google-auth chahiye)
  MAX_BOTS             default 40
Run:  python bot_manager.py
"""
import os, sys, re, time, json, signal, hashlib, logging, subprocess
import requests

DB_URL = os.environ.get("FIREBASE_DB_URL", "https://aryan-mods-4c084-default-rtdb.firebaseio.com").rstrip("/")
SECRET = os.environ.get("FIREBASE_DB_SECRET", "")
SA_JSON = os.environ.get("FIREBASE_SA_JSON", "")
MAX_BOTS = int(os.environ.get("MAX_BOTS", "40"))
POLL = 10
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "FF_PANEL_SELLING_BOT_FILE.py")
DATA = os.path.join(HERE, "bots_data")
os.makedirs(DATA, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [manager] %(message)s")
log = logging.getLogger("manager")

_sa = {"tok": None, "exp": 0}
def _auth():
    if SECRET:
        return {"auth": SECRET}
    if SA_JSON:
        if time.time() > _sa["exp"] - 120:
            from google.oauth2 import service_account
            import google.auth.transport.requests as gr
            c = service_account.Credentials.from_service_account_file(SA_JSON, scopes=[
                "https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"])
            c.refresh(gr.Request()); _sa["tok"], _sa["exp"] = c.token, time.time() + 3000
        return {"access_token": _sa["tok"]}
    return {}

def fb(method, path, body=None):
    r = requests.request(method, f"{DB_URL}/{path}.json", params=_auth(), json=body, timeout=20)
    r.raise_for_status()
    return r.json()

def safe(uid): return re.sub(r"[^A-Za-z0-9_-]", "_", str(uid))[:80]

VALID = {
    "token": lambda v: bool(re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", str(v))),
    "adminId": lambda v: bool(re.fullmatch(r"\d{5,15}", str(v))),
    "adminContact": lambda v: bool(re.fullmatch(r"@?[A-Za-z0-9_]{4,32}", str(v))),
    "botUsername": lambda v: bool(re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", str(v))),
    "shopName": lambda v: 2 <= len(str(v).strip()) <= 40,
}

crash_hist = {}  # uid -> crash timestamps
procs = {}      # uid -> {p, fp, started, crashes:[ts], log}
state = {}      # uid -> (status, msg, ts) last written
blocked_fp = {} # uid -> fingerprint that failed permanently (token invalid / too many crashes)

def fingerprint(b):
    k = "|".join(str(b.get(x, "")) for x in ("token", "adminId", "adminContact", "botUsername", "shopName", "rev"))
    return hashlib.sha1(k.encode()).hexdigest()

def set_status(uid, status, msg="", force=False):
    old = state.get(uid)
    if not force and old and old[0] == status and old[1] == msg and time.time() - old[2] < 60:
        return
    state[uid] = (status, msg, time.time())
    try:
        fb("PATCH", f"tg_bots/{uid}", {"status": status, "statusMsg": msg, "lastSeen": int(time.time() * 1000)})
    except Exception as e:
        log.warning("status write failed %s: %s", uid, e)

def stop(uid):
    h = procs.pop(uid, None)
    if not h: return
    try:
        h["p"].terminate(); h["p"].wait(timeout=8)
    except Exception:
        try: h["p"].kill()
        except Exception: pass
    try: h["log"].close()
    except Exception: pass
    log.info("stopped %s", uid)

def token_ok(token):
    """True / False (definitely invalid) / None (network problem)"""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        return bool(r.json().get("ok"))
    except Exception:
        return None

def start(uid, b, fp):
    name = safe(uid)
    lf = open(os.path.join(DATA, f"{name}.out"), "ab")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TZ", "SYSTEMROOT") if k in os.environ}
    env.update({
        "PYTHONUNBUFFERED": "1",
        "BOT_TOKEN": str(b["token"]).strip(),
        "BOT_USERNAME": str(b["botUsername"]).lstrip("@"),
        "ADMIN_ID": str(b["adminId"]).strip(),
        "ADMIN_CONTACT": "@" + str(b["adminContact"]).lstrip("@"),
        "SHOP_NAME": str(b["shopName"]).strip(),
        "DB_PATH": os.path.join(DATA, f"{name}.db"),
    })
    p = subprocess.Popen([sys.executable, SCRIPT], cwd=DATA, env=env, stdout=lf, stderr=subprocess.STDOUT)
    procs[uid] = {"p": p, "fp": fp, "started": time.time(), "crashes": crash_hist.get(uid, []), "log": lf}
    log.info("started %s pid=%s @%s", uid, p.pid, b.get("botUsername"))

def tail(uid, n=3):
    try:
        with open(os.path.join(DATA, f"{safe(uid)}.out"), "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 1500))
            lines = [l for l in f.read().decode("utf-8", "ignore").splitlines() if l.strip()]
        return " | ".join(lines[-n:])[:200]
    except Exception:
        return ""

def cycle():
    now_ms = int(time.time() * 1000)
    bots = fb("GET", "tg_bots") or {}
    seen_tokens = set()
    for uid, b in sorted(bots.items(), key=lambda kv: kv[1].get("createdAt", 0) if isinstance(kv[1], dict) else 0):
        if not isinstance(b, dict): continue
        uid_s = safe(uid)
        active = float(b.get("paidUntil") or 0) > now_ms
        want = active and b.get("desired", "on") != "off" and not b.get("blocked")
        fp = fingerprint(b)
        h = procs.get(uid)

        # crashed / exited?
        if h and h["p"].poll() is not None:
            code = h["p"].returncode; crashes = [t for t in h["crashes"] if time.time() - t < 600] + [time.time()]
            try: h["log"].close()
            except Exception: pass
            procs.pop(uid, None)
            h = None; crash_hist[uid] = crashes
            log.warning("%s exited code=%s", uid, code)
            if len(crashes) >= 5:
                blocked_fp[uid] = fp
                set_status(uid, "error", "Bot baar baar crash hua: " + tail(uid), True)
                continue

        if not want:
            if h: stop(uid)
            set_status(uid, "blocked" if b.get("blocked") else ("expired" if not active else "stopped"),
                       "Plan expire ho gaya" if not active else "")
            continue
        if not all(VALID[k](b.get(k, "")) for k in VALID):
            set_status(uid, "error", "Bot details adhoori/galat hain"); continue
        tok = str(b["token"]).strip()
        if tok in seen_tokens:
            if h: stop(uid)
            set_status(uid, "duplicate_token", "Ye token kisi aur user ke bot me chal raha hai"); continue
        seen_tokens.add(tok)

        if h and h["fp"] != fp:           # settings badli -> restart
            stop(uid); h = None
        if h:
            if time.time() - h["started"] > 20: set_status(uid, "running", "Bot chal raha hai")
            continue
        if blocked_fp.get(uid) == fp:     # permanent fail until user changes something
            continue
        blocked_fp.pop(uid, None)
        if len(procs) >= MAX_BOTS:
            set_status(uid, "queued", "Server full - thodi der baad"); continue
        ok = token_ok(tok)
        if ok is False:
            blocked_fp[uid] = fp; set_status(uid, "invalid_token", "Bot token galat hai - BotFather se naya token daalo", True); continue
        set_status(uid, "starting", "Bot start ho raha hai", True)
        start(uid, b, fp)

    for uid in list(procs):               # record delete ho gaya
        if uid not in bots: stop(uid)
    fb("PUT", "tg_manager", {"ts": now_ms, "running": len(procs), "host": os.uname().nodename if hasattr(os, "uname") else "server"})

def shutdown(*_):
    log.info("shutting down, stopping %d bots", len(procs))
    for uid in list(procs): stop(uid)
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown); signal.signal(signal.SIGINT, shutdown)
    if not os.path.exists(SCRIPT): sys.exit("FF_PANEL_SELLING_BOT_FILE.py isi folder me rakho")
    log.info("manager up. db=%s", DB_URL)
    while True:
        try: cycle()
        except Exception as e: log.error("cycle error: %s", e)
        time.sleep(POLL)
