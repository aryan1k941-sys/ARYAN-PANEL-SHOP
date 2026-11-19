#!/usr/bin/env python3
"""ARYAN PANEL SHOP reseller API.

Required Firebase mapping:
  api_clients/<firebase_uid>.botUid          = key in Firebase tg_bots (e.g. UID-620064-armanmalikh)
  api_clients/<firebase_uid>.telegramUserId  = numeric user_id in that bot's SQLite users table

The mapping is intentionally explicit: Firebase Auth UID is NOT the same as Telegram user_id.
"""
import os, re, time, json, hmac, sqlite3, logging, threading
from collections import defaultdict, deque
from datetime import datetime
from functools import wraps
from urllib.parse import quote
from contextlib import contextmanager
import requests
from flask import Flask, jsonify, request, g

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(APP_DIR, "bots_data"))
FIREBASE_DB_URL = os.environ.get(
    "FIREBASE_DB_URL",
    "https://aryan-mods-4c084-default-rtdb.firebaseio.com"
).rstrip("/")
FIREBASE_DB_SECRET = os.environ.get("FIREBASE_DB_SECRET", "")
FIREBASE_SA_JSON = os.environ.get("FIREBASE_SA_JSON", "")
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8080"))
API_MAX_BODY = int(os.environ.get("API_MAX_BODY", "16384"))
DEFAULT_RATE_LIMIT = int(os.environ.get("API_DEFAULT_RATE_LIMIT", "60"))
SUPPLIER_TIMEOUT = float(os.environ.get("SUPPLIER_TIMEOUT", "30"))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [aryan-api] %(levelname)s %(message)s")
log = logging.getLogger("aryan-api")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = API_MAX_BODY

rate_lock = threading.Lock()
rate_hits = defaultdict(deque)
sa_cache = {"credentials": None, "expiry": 0}

def api_error(message, status=400):
    return jsonify({"success": False, "error": message}), status

def firebase_auth_params():
    if FIREBASE_DB_SECRET:
        return {"auth": FIREBASE_DB_SECRET}
    if FIREBASE_SA_JSON:
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            if sa_cache["credentials"] is None or not sa_cache["credentials"].valid:
                creds = service_account.Credentials.from_service_account_file(
                    FIREBASE_SA_JSON,
                    scopes=["https://www.googleapis.com/auth/firebase.database",
                            "https://www.googleapis.com/auth/userinfo.email"])
                creds.refresh(Request())
                sa_cache["credentials"] = creds
            elif sa_cache["credentials"].expired:
                sa_cache["credentials"].refresh(Request())
            return {"access_token": sa_cache["credentials"].token}
        except Exception as exc:
            log.error("Firebase service-account auth failed: %s", exc)
            raise RuntimeError("Firebase authentication is not configured correctly")
    raise RuntimeError("Set FIREBASE_DB_SECRET or FIREBASE_SA_JSON")

def fb_get(path):
    url = f"{FIREBASE_DB_URL}/{path}.json"
    r = requests.get(url, params=firebase_auth_params(), timeout=10)
    r.raise_for_status()
    return r.json()

def fb_patch(path, body):
    url = f"{FIREBASE_DB_URL}/{path}.json"
    r = requests.patch(url, params=firebase_auth_params(), json=body, timeout=10)
    r.raise_for_status()
    return r.json()

def safe_uid(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value))[:80]

def db_path(bot_uid):
    path = os.path.abspath(os.path.join(DATA_DIR, safe_uid(bot_uid) + ".db"))
    root = os.path.abspath(DATA_DIR) + os.sep
    if not path.startswith(root):
        raise ValueError("Invalid bot mapping")
    if not os.path.isfile(path):
        raise FileNotFoundError("Mapped bot database was not found")
    return path

def connect_db(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn

def get_key_from_request():
    key = request.headers.get("X-API-Key", "").strip()
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key

def authenticate():
    key = get_key_from_request()
    if not key:
        return None, api_error("Missing X-API-Key header", 401)
    # API keys are stored under users/<firebase_uid>/apiKey. Never log key values.
    try:
        users = fb_get("users") or {}
        clients = fb_get("api_clients") or {}
    except Exception as exc:
        log.error("Firebase lookup failed: %s", exc)
        return None, api_error("Authentication service unavailable", 503)

    match_uid = None
    user_rec = None
    for uid, rec in users.items():
        if isinstance(rec, dict) and isinstance(rec.get("apiKey"), str):
            if hmac.compare_digest(rec["apiKey"], key):
                match_uid, user_rec = uid, rec
                break
    if not match_uid:
        return None, api_error("Invalid API key", 401)

    client = clients.get(match_uid) or {}
    if not isinstance(client, dict) or client.get("status") != "active":
        return None, api_error("API key is revoked or inactive", 403)

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    allowed = [x.strip() for x in str(client.get("ipAllow", "")).split(",") if x.strip()]
    if allowed and ip not in allowed:
        return None, api_error("IP address is not allowed", 403)

    limit = client.get("rateLimit", DEFAULT_RATE_LIMIT)
    try:
        limit = max(1, min(600, int(limit)))
    except (TypeError, ValueError):
        limit = DEFAULT_RATE_LIMIT
    now = time.time()
    with rate_lock:
        q = rate_hits[match_uid]
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= limit:
            return None, api_error("Rate limit exceeded", 429)
        q.append(now)

    bot_uid = str(client.get("botUid", "")).strip()
    tg_user_id = client.get("telegramUserId")
    try:
        tg_user_id = int(tg_user_id)
    except (TypeError, ValueError):
        return None, api_error(
            "Admin mapping required: set api_clients/<uid>/botUid and telegramUserId", 409)
    if not bot_uid:
        return None, api_error(
            "Admin mapping required: set api_clients/<uid>/botUid and telegramUserId", 409)

    return {
        "firebase_uid": match_uid, "web_user": user_rec, "client": client,
        "bot_uid": bot_uid, "telegram_user_id": tg_user_id, "ip": ip
    }, None

def require_client(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ctx, err = authenticate()
        if err:
            return err
        g.api_client = ctx
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                fb_patch(f"api_clients/{ctx['firebase_uid']}", {
                    "requests": int(ctx["client"].get("requests", 0) or 0) + 1,
                    "lastRequestAt": int(time.time() * 1000)
                })
            except Exception as exc:
                log.warning("Request counter update failed: %s", exc)
    return wrapper

def log_api(ctx, endpoint, status, details=""):
    # Keep API logs free of API keys, tokens, and delivered product keys.
    log.info("uid=%s endpoint=%s status=%s ip=%s %s",
             ctx.get("firebase_uid", "unknown"), endpoint, status,
             ctx.get("ip", ""), details[:160])

@contextmanager
def get_db_for_client(ctx):
    conn = connect_db(db_path(ctx["bot_uid"]))
    try:
        yield conn
    finally:
        conn.close()

def user_row(conn, tg_user_id):
    return conn.execute(
        "SELECT user_id, balance, is_reseller, is_banned FROM users WHERE user_id=?",
        (tg_user_id,)).fetchone()

def ensure_api_tables(conn):
    conn.execute("""
      CREATE TABLE IF NOT EXISTS api_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT UNIQUE,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        duration TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price_paid REAL NOT NULL,
        status TEXT NOT NULL,
        delivered_key TEXT DEFAULT '',
        error_message TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
      )
    """)
    conn.commit()

def supplier_buy(conn, product, duration, quantity):
    """Uses the bot's configured supplier settings, without returning raw supplier responses."""
    url_row = conn.execute("SELECT value FROM settings WHERE key='reseller_api_url'").fetchone()
    key_row = conn.execute("SELECT value FROM settings WHERE key='reseller_api_key'").fetchone()
    url = (url_row["value"] if url_row else "") or "https://bantibhaiya.to/api/reseller_v1.php"
    api_key = (key_row["value"] if key_row else "") or ""
    if not api_key or api_key == "YOUR_RRSELLER_API_ADD":
        return False, "", "Supplier API key is not configured"
    supplier_pid = str(product["api_product_id"] or "").strip()
    if not supplier_pid:
        return False, "", "This product has no supplier product ID"
    payload = {"api_key": api_key, "action": "buy", "product_id": supplier_pid,
               "duration": duration, "quantity": quantity}
    try:
        r = requests.post(url, data=payload,
                          headers={"X-API-Key": api_key, "Accept": "application/json"},
                          timeout=SUPPLIER_TIMEOUT)
        try:
            data = r.json()
        except ValueError:
            return False, "", f"Supplier returned non-JSON (HTTP {r.status_code})"
    except requests.RequestException:
        return False, "", "Supplier is unreachable"
    if isinstance(data, dict) and str(data.get("status", "")).lower() == "success":
        key = data.get("key")
        if not key and isinstance(data.get("keys"), list) and data["keys"]:
            key = data["keys"][0]
        if key:
            return True, str(key), ""
        return False, "", "Supplier reported success but did not return a key"
    if isinstance(data, dict):
        return False, "", str(data.get("message") or data.get("error") or "Supplier purchase failed")[:180]
    return False, "", "Supplier purchase failed"

@app.get("/health")
def health():
    return jsonify({"success": True, "service": "ARYAN PANEL SHOP API", "status": "online"})

@app.get("/api/v1/products")
@require_client
def products():
    ctx = g.api_client
    try:
        with get_db_for_client(ctx) as conn:
            rows = conn.execute("""
              SELECT id, category, panel_name, name, price_inr, reseller_price, stock,
                     validity, device_limit, api_product_id, api_duration
              FROM products WHERE COALESCE(is_active,1)=1 AND COALESCE(maintenance,0)=0
              ORDER BY category, id
            """).fetchall()
        data = [{
            "product_id": r["id"], "pid": r["id"], "name": r["name"],
            "panel_name": r["panel_name"], "category": r["category"],
            "price": float(r["reseller_price"] or r["price_inr"] or 0),
            "stock": int(r["stock"] or 0), "validity": r["validity"],
            "device_limit": r["device_limit"], "duration": r["api_duration"] or r["validity"]
        } for r in rows]
        log_api(ctx, request.path, 200)
        return jsonify({"success": True, "data": data})
    except Exception as exc:
        log.exception("Products endpoint failed")
        log_api(ctx, request.path, 500)
        return api_error("Could not read products", 500)

@app.get("/api/v1/balance")
@require_client
def balance():
    ctx = g.api_client
    try:
        with get_db_for_client(ctx) as conn:
            row = user_row(conn, ctx["telegram_user_id"])
        if not row:
            return api_error("Mapped Telegram user was not found", 404)
        if row["is_banned"]:
            return api_error("Account is blocked", 403)
        if not row["is_reseller"]:
            return api_error("Mapped Telegram account is not a reseller", 403)
        log_api(ctx, request.path, 200)
        return jsonify({"success": True, "data": {
            "balance": round(float(row["balance"] or 0), 2), "currency": "INR"
        }})
    except Exception:
        log.exception("Balance endpoint failed")
        return api_error("Could not read balance", 500)

@app.post("/api/v1/order")
@require_client
def create_order():
    ctx = g.api_client
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = request.form.to_dict()
    try:
        pid = int(body.get("product_id", body.get("pid")))
        qty = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return api_error("product_id/pid and quantity must be integers")
    # Existing supplier integration is verified only for a single returned key.
    if qty != 1:
        return api_error("quantity must be 1 until multi-key supplier responses are verified")
    duration = str(body.get("duration", "")).strip()[:60]
    request_id = str(request.headers.get("Idempotency-Key") or body.get("request_id") or "").strip()[:100]
    if not request_id:
        return api_error("Send a unique Idempotency-Key header or request_id to prevent duplicate purchases")
    try:
        path = db_path(ctx["bot_uid"])
        conn = connect_db(path)
        ensure_api_tables(conn)
        existing = conn.execute("SELECT * FROM api_orders WHERE request_id=?", (ctx["firebase_uid"] + ":" + request_id,)).fetchone()
        if existing:
            conn.close()
            return jsonify({"success": existing["status"] == "success", "data": {
                "order_id": existing["id"], "status": existing["status"],
                "product_id": existing["product_id"], "price_paid": existing["price_paid"],
                "key": existing["delivered_key"] if existing["status"] == "success" else None
            }})
        conn.execute("BEGIN IMMEDIATE")
        u = user_row(conn, ctx["telegram_user_id"])
        if not u:
            conn.rollback(); conn.close()
            return api_error("Mapped Telegram user was not found", 404)
        if u["is_banned"]:
            conn.rollback(); conn.close()
            return api_error("Account is blocked", 403)
        if not u["is_reseller"]:
            conn.rollback(); conn.close()
            return api_error("Mapped Telegram account is not a reseller", 403)
        p = conn.execute("""SELECT id, name, price_inr, reseller_price, stock, validity,
                           is_active, maintenance, api_product_id, api_duration
                           FROM products WHERE id=?""", (pid,)).fetchone()
        if not p or not p["is_active"] or p["maintenance"]:
            conn.rollback(); conn.close()
            return api_error("Product not found or unavailable", 404)
        unit_price = float(p["reseller_price"] or p["price_inr"] or 0)
        total = round(unit_price * qty, 2)
        if total <= 0:
            conn.rollback(); conn.close()
            return api_error("Product price is invalid", 409)
        if float(u["balance"] or 0) < total:
            conn.rollback(); conn.close()
            return api_error("Insufficient balance", 402)
        # Reserve funds before contacting supplier; refund on supplier failure.
        conn.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (total, ctx["telegram_user_id"]))
        cur = conn.execute("""INSERT INTO api_orders
          (request_id,user_id,product_id,product_name,duration,quantity,price_paid,status)
          VALUES (?,?,?,?,?,?,?,'processing')""",
          (ctx["firebase_uid"] + ":" + request_id, ctx["telegram_user_id"], pid,
           p["name"] or "", duration or p["api_duration"] or p["validity"] or "",
           qty, total))
        api_order_id = cur.lastrowid
        conn.commit()

        # Network call happens outside DB transaction. If the process dies here, the
        # processing row is intentionally left for manual reconciliation, not auto-refunded.
        ok, delivered, error = supplier_buy(conn, p, duration or p["api_duration"] or p["validity"] or "", qty)
        if not ok:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (total, ctx["telegram_user_id"]))
            conn.execute("UPDATE api_orders SET status='failed', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (error[:180], api_order_id))
            conn.commit()
            conn.close()
            log_api(ctx, request.path, 502, "supplier_failed")
            return api_error("Supplier purchase failed; reserved balance refunded", 502)

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO orders (user_id,product_name,price_paid,delivered_key,purchase_date)
                        VALUES (?,?,?,?,?)""",
                     (ctx["telegram_user_id"], p["name"] or "", total, delivered,
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.execute("UPDATE users SET orders_count=COALESCE(orders_count,0)+1, spent=COALESCE(spent,0)+? WHERE user_id=?",
                     (total, ctx["telegram_user_id"]))
        conn.execute("UPDATE api_orders SET status='success', delivered_key=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (delivered, api_order_id))
        conn.commit()
        conn.close()
        log_api(ctx, request.path, 200, "order_created")
        return jsonify({"success": True, "data": {
            "order_id": api_order_id, "status": "success", "product_id": pid,
            "quantity": qty, "price_paid": total, "key": delivered
        }})
    except Exception:
        log.exception("Order endpoint failed")
        try:
            conn.close()
        except Exception:
            pass
        return api_error("Order processing error; check server logs and api_orders status before retrying", 500)

@app.get("/api/v1/orders")
@require_client
def list_orders():
    ctx = g.api_client
    try:
        with get_db_for_client(ctx) as conn:
            ensure_api_tables(conn)
            rows = conn.execute("""SELECT id, product_id, product_name, duration, quantity,
                  price_paid, status, created_at FROM api_orders WHERE user_id=? ORDER BY id DESC LIMIT 100""",
                  (ctx["telegram_user_id"],)).fetchall()
        return jsonify({"success": True, "data": [dict(r) for r in rows]})
    except Exception:
        log.exception("Orders list failed")
        return api_error("Could not read orders", 500)

@app.get("/api/v1/order/<int:order_id>")
@require_client
def order_status(order_id):
    ctx = g.api_client
    try:
        with get_db_for_client(ctx) as conn:
            ensure_api_tables(conn)
            row = conn.execute("""SELECT id, product_id, product_name, duration, quantity,
                  price_paid, status, created_at, updated_at FROM api_orders
                  WHERE id=? AND user_id=?""", (order_id, ctx["telegram_user_id"])).fetchone()
        if not row:
            return api_error("Order not found", 404)
        return jsonify({"success": True, "data": dict(row)})
    except Exception:
        log.exception("Order status failed")
        return api_error("Could not read order status", 500)

@app.errorhandler(413)
def too_large(_):
    return api_error("Request body too large", 413)

@app.errorhandler(404)
def not_found(_):
    return api_error("Endpoint not found", 404)

if __name__ == "__main__":
    app.run(host=API_HOST, port=API_PORT, debug=False)
