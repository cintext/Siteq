#!/usr/bin/env python3
"""Small, dependency-free SITEQ API using SQLite and Python's standard library."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
import hashlib
import re
import ipaddress
from datetime import datetime, timezone
from contextlib import contextmanager
from datetime import timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SITEQ_DATABASE", str(ROOT / "backend" / "data" / "siteq.sqlite3"))).expanduser()
FRONTEND_PATH = ROOT / "index.html"
SCHEMA_PATH = ROOT / "backend" / "schema.sql"
HOST = os.environ.get("SITEQ_HOST", "127.0.0.1")
PORT = int(os.environ.get("SITEQ_PORT", "8765"))
ADMIN_USERNAME = os.environ.get("SITEQ_ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.environ.get("SITEQ_ADMIN_PASSWORD", "")
COOKIE_SECURE = os.environ.get("SITEQ_COOKIE_SECURE", "0") == "1"
TRUST_PROXY = os.environ.get("SITEQ_TRUST_PROXY", "0") == "1"
SESSION_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "siteq_session"
PASSWORD_ITERATIONS = 310_000
LOGIN_FAILURE_LIMIT = 8
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 15 * 60
ORDER_LIMIT = 5
ORDER_WINDOW_SECONDS = 10 * 60
ROLE_NAMES = {"owner": "Pemilik", "staff": "Petugas Kandang", "vet": "Dokter Hewan"}

INITIAL_PENS = [
    ("Kandang Melati", "Sapi", 12),
    ("Kandang Utara", "Sapi", 12),
    ("Kandang Selatan", "Sapi", 10),
    ("Kandang Timur", "Kambing", 15),
    ("Kandang Kambing", "Kambing", 15),
]
INITIAL_ANIMALS = [
    ("SQ-024", "Raka", "Sapi", "Simental", 30, 520, 28500000, "Kandang Melati", "Sehat"),
    ("SQ-031", "Bima", "Sapi", "Peranakan Ongole", 26, 440, 24750000, "Kandang Utara", "Sehat"),
    ("SQ-047", "Laras", "Kambing", "Boer", 15, 68, 4950000, "Kandang Kambing", "Sehat"),
    ("SQ-052", "Jati", "Sapi", "Brahman", 22, 390, 21900000, "Kandang Selatan", "Sehat"),
    ("SQ-061", "Pandan", "Kambing", "Kacang", 13, 34, 2850000, "Kandang Timur", "Sehat"),
    ("SQ-074", "Aruna", "Sapi", "Limousin", 28, 485, 26900000, "Kandang Utara", "Sehat"),
]
ANIMAL_FIELDS = ("name", "category", "breed", "age", "weight", "price", "pen", "health")
VALID_CATEGORIES = {"Sapi", "Kambing"}
VALID_HEALTH = {"Sehat", "Perawatan", "Karantina", "Terjual"}
VALID_ORDER_STATUS = {"Menunggu konfirmasi", "Dikonfirmasi", "Ditolak"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, expected_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(rounds)
        if not 100_000 <= iterations <= 600_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def add_audit(db: sqlite3.Connection, actor: sqlite3.Row, action: str, entity_type: str, entity_id: str, details: dict | None = None) -> None:
    db.execute(
        """INSERT INTO audit_logs(actor_user_id,actor_username,actor_role,action,entity_type,entity_id,details,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (actor["id"], actor["username"], actor["role"], action, entity_type, str(entity_id),
         json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")), utc_now()),
    )


def login_is_locked(remote_key: str) -> int:
    now = int(time.time())
    with connect() as db:
        row = db.execute("SELECT locked_until FROM login_attempts WHERE remote_key=?", (remote_key,)).fetchone()
    return max(0, int(row["locked_until"]) - now) if row else 0


def record_login_failure(remote_key: str) -> int:
    now = int(time.time())
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM login_attempts WHERE window_started<?", (now - 2 * LOGIN_WINDOW_SECONDS,))
        row = db.execute("SELECT failure_count,window_started FROM login_attempts WHERE remote_key=?", (remote_key,)).fetchone()
        if not row or now - int(row["window_started"]) > LOGIN_WINDOW_SECONDS:
            count, started = 1, now
        else:
            count, started = int(row["failure_count"]) + 1, int(row["window_started"])
        locked_until = now + LOGIN_LOCK_SECONDS if count >= LOGIN_FAILURE_LIMIT else 0
        db.execute(
            """INSERT INTO login_attempts(remote_key,failure_count,window_started,locked_until)
               VALUES(?,?,?,?) ON CONFLICT(remote_key) DO UPDATE SET
               failure_count=excluded.failure_count,window_started=excluded.window_started,locked_until=excluded.locked_until""",
            (remote_key, count, started, locked_until),
        )
    return max(0, locked_until - now)


def clear_login_failures(remote_key: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM login_attempts WHERE remote_key=?", (remote_key,))


def order_limit_wait(remote_key: str) -> int:
    now = int(time.time())
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM order_rate_limits WHERE window_started<?", (now - ORDER_WINDOW_SECONDS,))
        row = db.execute("SELECT request_count,window_started FROM order_rate_limits WHERE remote_key=?", (remote_key,)).fetchone()
        if not row or now - int(row["window_started"]) >= ORDER_WINDOW_SECONDS:
            db.execute(
                "INSERT INTO order_rate_limits(remote_key,request_count,window_started) VALUES(?,1,?) "
                "ON CONFLICT(remote_key) DO UPDATE SET request_count=1,window_started=excluded.window_started",
                (remote_key, now),
            )
            return 0
        if int(row["request_count"]) >= ORDER_LIMIT:
            return max(1, ORDER_WINDOW_SECONDS - (now - int(row["window_started"])))
        db.execute("UPDATE order_rate_limits SET request_count=request_count+1 WHERE remote_key=?", (remote_key,))
        return 0


def initialize_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        db.executescript("PRAGMA journal_mode = WAL;\n" + schema)
        seeded = db.execute("SELECT 1 FROM app_meta WHERE key='sample_data_seeded'").fetchone()
        if not seeded:
            is_empty = db.execute("SELECT COUNT(*) FROM pens").fetchone()[0] == 0 and db.execute("SELECT COUNT(*) FROM animals").fetchone()[0] == 0
            if is_empty:
                now = utc_now()
                db.executemany(
                    "INSERT INTO pens(name, category, capacity, created_at) VALUES (?, ?, ?, ?)",
                    [(name, category, capacity, now) for name, category, capacity in INITIAL_PENS],
                )
                db.executemany(
                    """INSERT INTO animals
                       (id, name, category, breed, age, weight, price, pen, health, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(*animal, now, now) for animal in INITIAL_ANIMALS],
                )
            db.execute("INSERT INTO app_meta(key,value) VALUES('sample_data_seeded',?)", (utc_now(),))
        user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0 and ADMIN_USERNAME and ADMIN_PASSWORD:
            if len(ADMIN_USERNAME) < 3 or len(ADMIN_USERNAME) > 40:
                raise SystemExit("SITEQ_ADMIN_USERNAME harus 3–40 karakter.")
            if len(ADMIN_PASSWORD) < 12:
                raise SystemExit("SITEQ_ADMIN_PASSWORD harus memiliki minimal 12 karakter.")
            now = utc_now()
            db.execute(
                """INSERT INTO users(username,display_name,password_hash,role,active,created_at,updated_at)
                   VALUES(?,?,?,'owner',1,?,?)""",
                (ADMIN_USERNAME, "Pemilik SITEQ", hash_password(ADMIN_PASSWORD), now, now),
            )


def as_animal(row: sqlite3.Row, db: sqlite3.Connection) -> dict:
    result = dict(row)
    result["price"] = int(result["price"])
    result["weight"] = float(result["weight"])
    result["available"] = (
        result["health"] not in {"Perawatan", "Karantina", "Terjual"}
        and db.execute(
            "SELECT 1 FROM orders WHERE animal_id = ? AND status IN ('Menunggu konfirmasi', 'Dikonfirmasi') LIMIT 1",
            (result["id"],),
        ).fetchone()
        is None
    )
    return result


def parse_animal(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Isi data ternak tidak valid.")
    name = str(payload.get("name", "")).strip()
    category = str(payload.get("category", "")).strip()
    breed = str(payload.get("breed", "")).strip()
    pen = str(payload.get("pen", "")).strip()
    health = str(payload.get("health", "Sehat")).strip()
    if not name or len(name) > 60:
        raise ValueError("Nama hewan wajib diisi (maksimal 60 karakter).")
    if category not in VALID_CATEGORIES:
        raise ValueError("Jenis hewan harus Sapi atau Kambing.")
    if not breed or len(breed) > 60:
        raise ValueError("Ras hewan wajib diisi (maksimal 60 karakter).")
    if not pen or len(pen) > 60:
        raise ValueError("Nama kandang wajib diisi.")
    if health not in VALID_HEALTH:
        raise ValueError("Status kesehatan tidak dikenal.")
    try:
        age = int(payload.get("age"))
        weight = float(payload.get("weight"))
        price = int(payload.get("price"))
    except (TypeError, ValueError):
        raise ValueError("Umur, bobot, dan harga harus berupa angka.") from None
    if not 1 <= age <= 500:
        raise ValueError("Umur harus antara 1 sampai 500 bulan.")
    if not 0 < weight <= 2000:
        raise ValueError("Bobot harus lebih dari 0 dan maksimal 2.000 kg.")
    if not 1 <= price <= 2_000_000_000:
        raise ValueError("Harga harus lebih dari 0.")
    return {"name": name, "category": category, "breed": breed, "age": age, "weight": weight, "price": price, "pen": pen, "health": health}


class SiteqHandler(BaseHTTPRequestHandler):
    server_version = "SITEQ/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status: int, data: dict | list, headers: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if COOKIE_SECURE:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json(status, {"error": message})

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Panjang request tidak valid.") from None
        if length < 1 or length > 1_000_000:
            raise ValueError("Request harus berisi JSON dan maksimal 1 MB.")
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("Content-Type harus application/json.")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("JSON tidak valid.") from None
        if not isinstance(value, dict):
            raise ValueError("JSON harus berupa object.")
        return value

    def path_parts(self) -> list[str]:
        return [part for part in urlparse(self.path).path.strip("/").split("/") if part]

    def remote_key(self) -> str:
        if TRUST_PROXY:
            forwarded = self.headers.get("X-Forwarded-For", "")
            candidate = forwarded.split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        return self.client_address[0]

    def current_user(self) -> dict | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get(SESSION_COOKIE)
            token = morsel.value if morsel else ""
        except Exception:
            token = ""
        if not token:
            return None
        with connect() as db:
            row = db.execute(
                """SELECT u.id,u.username,u.display_name,u.role,s.expires_at
                   FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND u.active=1""",
                (token_digest(token),),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] <= utc_now():
                db.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(token),))
                return None
        return dict(row)

    def current_token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else ""
        except Exception:
            return ""

    def require_roles(self, *roles: str) -> dict | None:
        user = self.current_user()
        if not user:
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "Masuk sebagai pengurus untuk melanjutkan.")
            return None
        if user["role"] not in roles:
            self.send_error_json(HTTPStatus.FORBIDDEN, "Akun ini tidak memiliki hak untuk tindakan tersebut.")
            return None
        return user

    def origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host or origin == "null":
            return False
        try:
            parsed = urlparse(origin)
            return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()
        except ValueError:
            return False

    def do_OPTIONS(self) -> None:
        self.send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "Metode tidak didukung.")

    def do_GET(self) -> None:
        parts = self.path_parts()
        path = "/" + "/".join(parts)
        if path in {"/", "/index.html"}:
            try:
                body = FRONTEND_PATH.read_bytes()
            except OSError:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "File index.html tidak ditemukan.")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            if COOKIE_SECURE:
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/backend/client.js":
            try:
                body = (ROOT / "backend" / "client.js").read_bytes()
            except OSError:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "File frontend backend tidak ditemukan.")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            self.send_json(HTTPStatus.OK, {"ok": True, "service": "siteq-api", "time": utc_now()})
            return
        if path == "/api/animals":
            with connect() as db:
                rows = db.execute("SELECT * FROM animals ORDER BY created_at DESC, id").fetchall()
                self.send_json(HTTPStatus.OK, [as_animal(row, db) for row in rows])
            return
        if path == "/api/pens":
            with connect() as db:
                rows = db.execute(
                    """SELECT p.name, p.category, p.capacity,
                              (SELECT COUNT(*) FROM animals a WHERE a.pen=p.name) AS occupied
                       FROM pens p ORDER BY p.name"""
                ).fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) for row in rows])
            return
        if path == "/api/admin/session":
            user = self.current_user()
            if not user:
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "Sesi pengurus tidak aktif.")
                return
            self.send_json(HTTPStatus.OK, {"authenticated": True, **{key: user[key] for key in ("id", "username", "display_name", "role")}, "role_name": ROLE_NAMES[user["role"]]})
            return
        if path == "/api/admin/orders":
            if not self.require_roles("owner", "staff"):
                return
            with connect() as db:
                rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) for row in rows])
            return
        if path == "/api/admin/users":
            if not self.require_roles("owner"):
                return
            with connect() as db:
                rows = db.execute("SELECT id,username,display_name,role,active,created_at FROM users ORDER BY display_name").fetchall()
            self.send_json(HTTPStatus.OK, [{**dict(row), "role_name": ROLE_NAMES[row["role"]]} for row in rows])
            return
        if path == "/api/admin/audit":
            if not self.require_roles("owner"):
                return
            with connect() as db:
                rows = db.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 200").fetchall()
            self.send_json(HTTPStatus.OK, [dict(row) for row in rows])
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Rute tidak ditemukan.")

    def do_POST(self) -> None:
        path = "/" + "/".join(self.path_parts())
        if not self.origin_allowed():
            self.send_error_json(HTTPStatus.FORBIDDEN, "Request lintas origin ditolak.")
            return
        try:
            payload = self.read_json()
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return

        if path == "/api/admin/login":
            username = str(payload.get("username", "")).strip()[:80]
            password = str(payload.get("password", ""))[:200]
            remote_key = self.remote_key()
            wait_seconds = login_is_locked(remote_key)
            if wait_seconds:
                self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Terlalu banyak percobaan masuk. Coba lagi beberapa menit."}, {"Retry-After": str(wait_seconds)})
                return
            with connect() as db:
                user = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (username,)).fetchone()
            dummy_hash = "pbkdf2_sha256$310000$" + ("00" * 16) + "$" + ("00" * 32)
            valid_password = verify_password(password, user["password_hash"] if user else dummy_hash)
            if not user or not valid_password:
                wait_seconds = record_login_failure(remote_key)
                headers = {"Retry-After": str(wait_seconds)} if wait_seconds else None
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Username atau password salah."}, headers)
                return
            clear_login_failures(remote_key)
            token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            expires = (now + timedelta(seconds=SESSION_SECONDS)).isoformat(timespec="seconds")
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE expires_at<=?", (utc_now(),))
                db.execute(
                    "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                    (token_digest(token), user["id"], now.isoformat(timespec="seconds"), expires),
                )
                add_audit(db, user, "login", "session", str(user["id"]))
            cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS}"
            if COOKIE_SECURE:
                cookie += "; Secure"
            self.send_json(HTTPStatus.OK, {"authenticated": True, "user": {"id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"], "role_name": ROLE_NAMES[user["role"]]}}, {"Set-Cookie": cookie})
            return
        if path == "/api/admin/logout":
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                morsel = cookie.get(SESSION_COOKIE)
                token = morsel.value if morsel else ""
            except Exception:
                token = ""
            user = self.current_user()
            with connect() as db:
                if token:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(token),))
                if user:
                    add_audit(db, user, "logout", "session", str(user["id"]))
            expire = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + ("; Secure" if COOKIE_SECURE else "")
            self.send_json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": expire})
            return
        if path == "/api/orders":
            wait_seconds = order_limit_wait(self.remote_key())
            if wait_seconds:
                self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Terlalu banyak permintaan dari alamat ini. Coba lagi nanti."}, {"Retry-After": str(wait_seconds)})
                return
            self.create_order(payload)
            return
        if path == "/api/admin/animals":
            actor = self.require_roles("owner", "staff")
            if not actor:
                return
            self.create_animal(payload, actor)
            return
        if path == "/api/admin/pens":
            actor = self.require_roles("owner", "staff")
            if not actor:
                return
            self.create_pen(payload, actor)
            return
        if path == "/api/admin/users":
            actor = self.require_roles("owner")
            if not actor:
                return
            self.create_user(payload, actor)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Rute tidak ditemukan.")

    def do_PATCH(self) -> None:
        if not self.origin_allowed():
            self.send_error_json(HTTPStatus.FORBIDDEN, "Request lintas origin ditolak.")
            return
        parts = self.path_parts()
        try:
            payload = self.read_json()
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return
        if len(parts) == 4 and parts[:3] == ["api", "admin", "animals"]:
            actor = self.require_roles("owner", "staff")
            if not actor:
                return
            self.update_animal(parts[3], payload, actor)
            return
        if len(parts) == 4 and parts[:3] == ["api", "admin", "orders"]:
            actor = self.require_roles("owner", "staff")
            if not actor:
                return
            self.update_order(parts[3], payload, actor)
            return
        if len(parts) == 5 and parts[:3] == ["api", "vet", "animals"] and parts[4] == "health":
            actor = self.require_roles("owner", "vet")
            if not actor:
                return
            self.update_animal_health(parts[3], payload, actor)
            return
        if parts == ["api", "admin", "profile"]:
            actor = self.current_user()
            if not actor:
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "Masuk sebagai pengurus untuk melanjutkan.")
                return
            self.change_password(payload, actor)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Rute tidak ditemukan.")

    def do_DELETE(self) -> None:
        if not self.origin_allowed():
            self.send_error_json(HTTPStatus.FORBIDDEN, "Request lintas origin ditolak.")
            return
        parts = self.path_parts()
        if len(parts) != 4:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Rute tidak ditemukan.")
            return
        if parts[:3] == ["api", "admin", "animals"]:
            actor = self.require_roles("owner", "staff")
            if not actor:
                return
            with connect() as db:
                try:
                    result = db.execute("DELETE FROM animals WHERE id=?", (parts[3],))
                except sqlite3.IntegrityError:
                    self.send_error_json(HTTPStatus.CONFLICT, "Hewan memiliki riwayat permintaan dan tidak dapat dihapus.")
                    return
                if result.rowcount == 0:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "Hewan tidak ditemukan.")
                    return
                add_audit(db, actor, "delete", "animal", parts[3])
            self.send_json(HTTPStatus.OK, {"deleted": parts[3]})
            return
        if parts[:3] == ["api", "admin", "users"]:
            actor = self.require_roles("owner")
            if not actor:
                return
            if not parts[3].isdigit() or len(parts[3]) > 12:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "ID pengguna tidak valid.")
                return
            self.delete_user(int(parts[3]), actor)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Rute tidak ditemukan.")

    def change_password(self, payload: dict, actor: dict) -> None:
        current_password = str(payload.get("current_password", ""))
        new_password = str(payload.get("new_password", ""))
        if len(new_password) < 12 or len(new_password) > 200:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Password baru harus 12–200 karakter.")
            return
        token_hash = token_digest(self.current_token())
        with connect() as db:
            user = db.execute("SELECT password_hash FROM users WHERE id=? AND active=1", (actor["id"],)).fetchone()
            if not user or not verify_password(current_password, user["password_hash"]):
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "Password saat ini tidak cocok.")
                return
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(new_password), utc_now(), actor["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (actor["id"], token_hash))
            add_audit(db, actor, "change_password", "user", str(actor["id"]))
        self.send_json(HTTPStatus.OK, {"ok": True, "message": "Password diperbarui. Sesi lain sudah dikeluarkan."})

    def create_user(self, payload: dict, actor: dict) -> None:
        display_name = str(payload.get("display_name", "")).strip()
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        role = str(payload.get("role", ""))
        if not 2 <= len(display_name) <= 80:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Nama pengguna harus 2–80 karakter.")
            return
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", username):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Username harus 3–40 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
            return
        if len(password) < 12 or len(password) > 200:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Password harus 12–200 karakter.")
            return
        if role not in ROLE_NAMES:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Peran pengguna tidak dikenal.")
            return
        now = utc_now()
        with connect() as db:
            try:
                result = db.execute(
                    "INSERT INTO users(username,display_name,password_hash,role,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                    (username, display_name, hash_password(password), role, now, now),
                )
            except sqlite3.IntegrityError:
                self.send_error_json(HTTPStatus.CONFLICT, "Username sudah digunakan.")
                return
            user_id = result.lastrowid
            add_audit(db, actor, "create", "user", str(user_id), {"username": username, "role": role})
        self.send_json(HTTPStatus.CREATED, {"id": user_id, "username": username, "display_name": display_name, "role": role, "role_name": ROLE_NAMES[role], "active": 1})

    def delete_user(self, user_id: int, actor: dict) -> None:
        if user_id == actor["id"]:
            self.send_error_json(HTTPStatus.CONFLICT, "Akun yang sedang dipakai tidak dapat dihapus.")
            return
        with connect() as db:
            target = db.execute("SELECT id,username,role FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Pengguna tidak ditemukan.")
                return
            if target["role"] == "owner":
                owners = db.execute("SELECT COUNT(*) FROM users WHERE role='owner' AND active=1").fetchone()[0]
                if owners <= 1:
                    self.send_error_json(HTTPStatus.CONFLICT, "Sisakan minimal satu akun pemilik yang aktif.")
                    return
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            add_audit(db, actor, "delete", "user", str(user_id), {"username": target["username"], "role": target["role"]})
        self.send_json(HTTPStatus.OK, {"deleted": user_id})

    def create_order(self, payload: dict) -> None:
        animal_id = str(payload.get("animal_id", "")).strip()
        buyer_name = str(payload.get("buyer_name", "")).strip()
        buyer_phone = str(payload.get("buyer_phone", "")).strip()
        note = str(payload.get("note", "")).strip()
        if not animal_id or not buyer_name or not buyer_phone:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Pilih hewan dan isi nama serta nomor WhatsApp.")
            return
        if len(buyer_name) > 80 or len(buyer_phone) > 25 or len(note) > 300:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Panjang nama, nomor, atau catatan melebihi batas.")
            return
        order_id, now = f"ORD-{secrets.token_hex(6).upper()}", utc_now()
        try:
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                animal = db.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
                if not animal:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "Hewan tidak ditemukan.")
                    return
                if animal["health"] != "Sehat":
                    self.send_error_json(HTTPStatus.CONFLICT, "Hewan ini sedang tidak tersedia.")
                    return
                db.execute(
                    """INSERT INTO orders(id, animal_id, animal_name, category, buyer_name,
                       buyer_phone, note, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'Menunggu konfirmasi', ?, ?)""",
                    (order_id, animal_id, animal["name"], animal["category"], buyer_name, buyer_phone, note, now, now),
                )
                db.execute(
                    """INSERT INTO audit_logs(actor_user_id,actor_username,actor_role,action,entity_type,entity_id,details,created_at)
                       VALUES(NULL,'pembeli','buyer','create','order',?,?,?)""",
                    (order_id, json.dumps({"animal_id": animal_id}, separators=(",", ":")), now),
                )
        except sqlite3.IntegrityError:
            self.send_error_json(HTTPStatus.CONFLICT, "Hewan sudah memiliki permintaan aktif.")
            return
        self.send_json(HTTPStatus.CREATED, {"id": order_id, "animal_id": animal_id, "status": "Menunggu konfirmasi", "created_at": now})

    def create_animal(self, payload: dict, actor: dict) -> None:
        try:
            animal = parse_animal(payload)
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return
        now = utc_now()
        with connect() as db:
            pen = db.execute("SELECT * FROM pens WHERE name=?", (animal["pen"],)).fetchone()
            if not pen or pen["category"] != animal["category"]:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Pilih kandang dengan jenis ternak yang sesuai.")
                return
            occupied = db.execute("SELECT COUNT(*) FROM animals WHERE pen=?", (animal["pen"],)).fetchone()[0]
            if occupied >= pen["capacity"]:
                self.send_error_json(HTTPStatus.CONFLICT, "Kandang sudah mencapai kapasitas.")
                return
            next_number = db.execute(
                "SELECT COALESCE(MAX(CAST(SUBSTR(id, 4) AS INTEGER)), 0) + 1 FROM animals WHERE id GLOB 'SQ-[0-9]*'"
            ).fetchone()[0]
            animal_id = f"SQ-{next_number:03d}"
            db.execute(
                """INSERT INTO animals(id,name,category,breed,age,weight,price,pen,health,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (animal_id, *(animal[field] for field in ANIMAL_FIELDS), now, now),
            )
            add_audit(db, actor, "create", "animal", animal_id)
        self.send_json(HTTPStatus.CREATED, {"id": animal_id, **animal, "created_at": now, "updated_at": now})

    def update_animal(self, animal_id: str, payload: dict, actor: dict) -> None:
        try:
            animal = parse_animal(payload)
        except ValueError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return
        now = utc_now()
        with connect() as db:
            if not db.execute("SELECT 1 FROM animals WHERE id=?", (animal_id,)).fetchone():
                self.send_error_json(HTTPStatus.NOT_FOUND, "Hewan tidak ditemukan.")
                return
            pen = db.execute("SELECT * FROM pens WHERE name=?", (animal["pen"],)).fetchone()
            if not pen or pen["category"] != animal["category"]:
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Pilih kandang dengan jenis ternak yang sesuai.")
                return
            occupied = db.execute("SELECT COUNT(*) FROM animals WHERE pen=? AND id<>?", (animal["pen"], animal_id)).fetchone()[0]
            if occupied >= pen["capacity"]:
                self.send_error_json(HTTPStatus.CONFLICT, "Kandang sudah mencapai kapasitas.")
                return
            db.execute(
                """UPDATE animals SET name=?,category=?,breed=?,age=?,weight=?,price=?,pen=?,health=?,updated_at=? WHERE id=?""",
                (*(animal[field] for field in ANIMAL_FIELDS), now, animal_id),
            )
            add_audit(db, actor, "update", "animal", animal_id)
        self.send_json(HTTPStatus.OK, {"id": animal_id, **animal, "updated_at": now})

    def update_animal_health(self, animal_id: str, payload: dict, actor: dict) -> None:
        health = str(payload.get("health", "")).strip()
        if health not in {"Sehat", "Perawatan", "Karantina"}:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Dokter hewan dapat memilih Sehat, Perawatan, atau Karantina.")
            return
        now = utc_now()
        with connect() as db:
            animal = db.execute("SELECT health FROM animals WHERE id=?", (animal_id,)).fetchone()
            if not animal:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Hewan tidak ditemukan.")
                return
            db.execute("UPDATE animals SET health=?,updated_at=? WHERE id=?", (health, now, animal_id))
            add_audit(db, actor, "update_health", "animal", animal_id, {"from": animal["health"], "to": health})
        self.send_json(HTTPStatus.OK, {"id": animal_id, "health": health, "updated_at": now})

    def create_pen(self, payload: dict, actor: dict) -> None:
        name = str(payload.get("name", "")).strip()
        category = str(payload.get("category", "")).strip()
        try:
            capacity = int(payload.get("capacity"))
        except (TypeError, ValueError):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Kapasitas harus berupa angka.")
            return
        if not name or len(name) > 60 or category not in VALID_CATEGORIES or not 1 <= capacity <= 500:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Nama, jenis, atau kapasitas kandang tidak valid.")
            return
        try:
            with connect() as db:
                db.execute("INSERT INTO pens(name,category,capacity,created_at) VALUES(?,?,?,?)", (name, category, capacity, utc_now()))
                add_audit(db, actor, "create", "pen", name)
        except sqlite3.IntegrityError:
            self.send_error_json(HTTPStatus.CONFLICT, "Nama kandang sudah digunakan.")
            return
        self.send_json(HTTPStatus.CREATED, {"name": name, "category": category, "capacity": capacity, "occupied": 0})

    def update_order(self, order_id: str, payload: dict, actor: dict) -> None:
        status = str(payload.get("status", ""))
        if status not in VALID_ORDER_STATUS:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Status permintaan tidak dikenal.")
            return
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Permintaan tidak ditemukan.")
                return
            if status in {"Menunggu konfirmasi", "Dikonfirmasi"}:
                conflict = db.execute(
                    "SELECT 1 FROM orders WHERE animal_id=? AND id<>? AND status IN ('Menunggu konfirmasi','Dikonfirmasi') LIMIT 1",
                    (order["animal_id"], order_id),
                ).fetchone()
                if conflict:
                    self.send_error_json(HTTPStatus.CONFLICT, "Sudah ada permintaan aktif untuk hewan ini.")
                    return
            db.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (status, utc_now(), order_id))
            add_audit(db, actor, "update", "order", order_id, {"from": order["status"], "to": status})
        self.send_json(HTTPStatus.OK, {"id": order_id, "status": status})


def main() -> None:
    initialize_database()
    with connect() as db:
        owner_exists = db.execute("SELECT 1 FROM users WHERE role='owner' AND active=1 LIMIT 1").fetchone()
    if not owner_exists:
        sys.stderr.write(
            "Belum ada akun pemilik aktif. Pada database baru, set SITEQ_ADMIN_USERNAME dan "
            "SITEQ_ADMIN_PASSWORD (minimal 12 karakter) untuk membuat akun pertama.\n"
        )
        raise SystemExit(2)
    server = ThreadingHTTPServer((HOST, PORT), SiteqHandler)
    sys.stderr.write(f"SITEQ berjalan di http://{HOST}:{PORT}\nDatabase: {DB_PATH}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nSITEQ dihentikan.\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
