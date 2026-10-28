#!/usr/bin/env python3
"""Small Airport Tycoon: game client, accounts, and server-side saves.

The game itself is static HTML. This Flask app gates index.html behind a
session cookie and stores one JSON save per account in SQLite. It is meant to
sit behind Cloudflare Tunnel like the other apps on the web LXC.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AIRPORT_DATA_DIR", str(ROOT / "data")))
DB_PATH = DATA_DIR / "airport-tycoon.sqlite"
SECRET_FILE = DATA_DIR / "secret_key"
PORT = int(os.environ.get("PORT", "8123"))

USER_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
PASSWORD_MIN = 8
GRID = 48
MAX_BUILDINGS = GRID * GRID
MAX_SAVE_BYTES = 256 * 1024
LOGIN_FAIL_WINDOW = 900
LOGIN_FAIL_MAX = 8

BUILDING_IDS = {
    "runway",
    "threshold",
    "grass",
    "taxi",
    "taxiTurn",
    "apron",
    "helipad",
    "hangar",
    "tHangar",
    "tower",
    "club",
    "fuel",
    "windsock",
    "beacon",
}

SOUND_DIR = ROOT / "sounds"
SOUND_FILES = {"prop-loop.wav", "jet-loop.wav", "jet-takeoff.wav"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saves (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def _secret_key() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_FILE.write_text(key)
    SECRET_FILE.chmod(0o600)
    return key


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_error(message: str, status: int):
    return jsonify(error=message), status


app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = _secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=MAX_SAVE_BYTES + 4096,
)


# Hashing a throwaway password keeps the "no such user" branch of login as slow
# as a real check, so response time does not leak which usernames exist.
DUMMY_HASH = generate_password_hash("airport-tycoon-no-such-user")

_login_fails: dict[tuple[str, str], list[float]] = {}
_login_fails_lock = threading.Lock()


def _login_key(username: str) -> tuple[str, str]:
    return (request.remote_addr or "?", username)


def _login_blocked(username: str) -> bool:
    cutoff = time.time() - LOGIN_FAIL_WINDOW
    with _login_fails_lock:
        for key in list(_login_fails):
            kept = [t for t in _login_fails[key] if t > cutoff]
            if kept:
                _login_fails[key] = kept
            else:
                del _login_fails[key]
        return len(_login_fails.get(_login_key(username), [])) >= LOGIN_FAIL_MAX


def _note_login_fail(username: str) -> None:
    with _login_fails_lock:
        _login_fails.setdefault(_login_key(username), []).append(time.time())


def _clear_login_fails(username: str) -> None:
    with _login_fails_lock:
        _login_fails.pop(_login_key(username), None)


def db() -> sqlite3.Connection:
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_PATH, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(SCHEMA)
        g.db = con
    return g.db


@app.teardown_appcontext
def _close_db(_exc) -> None:
    con = g.pop("db", None)
    if con is not None:
        con.close()


def current_user():
    uid = session.get("user_id")
    if not isinstance(uid, int):
        return None
    row = db().execute(
        "SELECT id, username FROM users WHERE id = ?", (uid,)
    ).fetchone()
    if row is None:
        session.clear()
    return row


def clean_save(doc):
    """Validate and normalize a client save before it reaches SQLite."""
    if not isinstance(doc, dict):
        return None
    cash = doc.get("cash")
    day = doc.get("day")
    buildings = doc.get("buildings")
    if isinstance(cash, bool) or not isinstance(cash, (int, float)):
        return None
    if isinstance(day, bool) or not isinstance(day, (int, float)):
        return None
    if not math.isfinite(cash) or not math.isfinite(day):
        return None
    if not (-1e12 <= cash <= 1e12) or not (0 <= day <= 1e9):
        return None
    if not isinstance(buildings, list) or len(buildings) > MAX_BUILDINGS:
        return None
    cleaned = []
    for b in buildings:
        if not isinstance(b, dict):
            return None
        bid = b.get("id")
        cx = b.get("cx")
        cz = b.get("cz")
        yaw = b.get("yaw")
        cost = b.get("cost")
        if bid not in BUILDING_IDS:
            return None
        if isinstance(cx, bool) or not isinstance(cx, int):
            return None
        if isinstance(cz, bool) or not isinstance(cz, int):
            return None
        if isinstance(yaw, bool) or not isinstance(yaw, int):
            return None
        if not (0 <= cx < GRID and 0 <= cz < GRID and 0 <= yaw < 4):
            return None
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            return None
        if not math.isfinite(cost) or not (0 <= cost <= 1e9):
            return None
        cleaned.append({"id": bid, "cx": cx, "cz": cz, "yaw": yaw, "cost": cost})
    return {"cash": cash, "day": int(day), "buildings": cleaned}


@app.before_request
def _before_request():
    # Cloudflare Tunnel terminates TLS and sets X-Forwarded-Proto; ProxyFix maps
    # it to request.is_secure. Plain LAN/dev HTTP keeps a non-Secure cookie.
    app.config["SESSION_COOKIE_SECURE"] = request.is_secure

    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            return json_error("Cross-origin request refused.", 403)
    return None


@app.after_request
def _headers(resp):
    if request.path.startswith("/api/") or resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store"
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.errorhandler(413)
def _too_large(err):
    if request.path.startswith("/api/"):
        return json_error("Request is too large.", 413)
    return err


@app.get("/healthz")
def healthz():
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/")
def index():
    if current_user() is None:
        return redirect(url_for("login_page"))
    return send_from_directory(str(ROOT), "index.html")


@app.get("/index.html")
def index_html():
    return index()


@app.get("/sounds/<path:filename>")
def sound_file(filename: str):
    if filename not in SOUND_FILES:
        return json_error("Not found.", 404)
    resp = send_from_directory(str(SOUND_DIR), filename, mimetype="audio/wav")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.get("/login")
def login_page():
    if current_user() is not None:
        return redirect(url_for("index"))
    return send_from_directory(str(ROOT), "login.html")


@app.get("/login.html")
def login_html():
    return login_page()


@app.post("/api/register")
def api_register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return json_error("Expected JSON.", 400)
    username = str(data.get("username") or "").strip().lower()
    password = data.get("password")
    if not USER_RE.match(username):
        return json_error("Username: 3-32 letters, numbers, . _ -", 400)
    if not isinstance(password, str) or len(password) < PASSWORD_MIN:
        return json_error(f"Password: at least {PASSWORD_MIN} characters.", 400)
    if len(password) > 200:
        return json_error("Password is too long.", 400)

    con = db()
    try:
        cur = con.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), utcnow()),
        )
        con.commit()
    except sqlite3.IntegrityError:
        return json_error("That username is taken.", 409)

    session.clear()
    session["user_id"] = cur.lastrowid
    session.permanent = True
    return jsonify(user={"username": username}), 201


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return json_error("Expected JSON.", 400)
    username = str(data.get("username") or "").strip().lower()
    password = data.get("password")
    if not isinstance(password, str):
        password = ""
    if _login_blocked(username):
        return json_error("Too many attempts. Try again later.", 429)

    row = db().execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    valid = check_password_hash(
        row["password_hash"] if row else DUMMY_HASH, password
    )
    if row is None or not valid:
        _note_login_fail(username)
        return json_error("Invalid username or password.", 401)

    _clear_login_fails(username)
    session.clear()
    session["user_id"] = row["id"]
    session.permanent = True
    return jsonify(user={"username": row["username"]})


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/session")
def api_session():
    user = current_user()
    if user is None:
        return json_error("Not signed in.", 401)
    return jsonify(user={"username": user["username"]})


@app.get("/api/save")
def api_get_save():
    user = current_user()
    if user is None:
        return json_error("Not signed in.", 401)
    row = db().execute(
        "SELECT data, updated_at FROM saves WHERE user_id = ?", (user["id"],)
    ).fetchone()
    if row is None:
        return jsonify(save=None, updated_at=None)
    try:
        save = json.loads(row["data"])
    except json.JSONDecodeError:
        return jsonify(save=None, updated_at=row["updated_at"])
    return jsonify(save=save, updated_at=row["updated_at"])


@app.put("/api/save")
def api_put_save():
    user = current_user()
    if user is None:
        return json_error("Not signed in.", 401)
    if not request.is_json:
        return json_error("Expected JSON.", 415)
    cleaned = clean_save(request.get_json(silent=True))
    if cleaned is None:
        return json_error("Invalid save data.", 400)
    body = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_SAVE_BYTES:
        return json_error("Save data is too large.", 413)

    now = utcnow()
    con = db()
    con.execute(
        "INSERT INTO saves (user_id, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, "
        "updated_at = excluded.updated_at",
        (user["id"], body, now),
    )
    con.commit()
    return jsonify(ok=True, updated_at=now)


if __name__ == "__main__":
    with app.app_context():
        db().close()
    print(f"Airport tycoon at http://localhost:{PORT}/")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
