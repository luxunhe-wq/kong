"""Persistent accounts, password hashing and revocable sessions for MoniLite."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

PASSWORD_ROUNDS = 600_000
SESSION_SECONDS = 12 * 3600


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS)
    return f"pbkdf2_sha256${PASSWORD_ROUNDS}${salt}${digest.hex()}"


def password_matches(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def credentials(username, password):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}", username):
        raise AuthError("账号需为 3–32 位字母、数字、下划线、点或短横线，首位不能是点或短横线")
    validate_password(password)
    return username


def validate_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise AuthError("密码需为 10–128 个字符")


def public_user(row):
    return {key: row[key] for key in ("id", "username", "role", "status", "created_at", "last_login")}


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = defaultdict(deque)

    def check(self, key, limit=8, window=300):
        now = time.monotonic()
        with self.lock:
            # Keep storage bounded even if requests originate from many addresses.
            if len(self.attempts) > 2000:
                for old_key in list(self.attempts):
                    if not self.attempts[old_key] or now - self.attempts[old_key][-1] > 300:
                        del self.attempts[old_key]
                if len(self.attempts) > 2000:
                    raise AuthError("请求过于频繁，请稍后再试", 429)
            attempts = self.attempts[key]
            while attempts and now - attempts[0] > window:
                attempts.popleft()
            if len(attempts) >= limit:
                raise AuthError("尝试次数过多，请 5 分钟后再试", 429)
            attempts.append(now)


class AuthStore:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Reuse an existing database in place, including any SQLite journal files.
        # Renaming or copying a live database could lose accounts or sessions.
        legacy_path = directory / "kong.sqlite3"
        self.path = legacy_path if legacy_path.exists() else directory / "monilite.sqlite3"
        self.limiter = RateLimiter()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
                    status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'disabled')),
                    created_at REAL NOT NULL,
                    last_login REAL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES ('registration_enabled', 'true');
            """)
        os.chmod(self.path, 0o600)
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def status(self, session=None):
        with self.connection() as db:
            initialized = db.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0] == 1
            enabled = db.execute("SELECT value FROM settings WHERE key='registration_enabled'").fetchone()[0] == "true"
        return {"initialized": initialized, "registration_enabled": enabled,
                "user": session["user"] if session else None,
                "csrf_token": session["csrf_token"] if session else None}

    def session(self, token):
        if not token or len(token) > 128:
            return None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute("""SELECT u.*, s.csrf_token, s.expires_at FROM sessions s
                JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'""",
                (hashed, time.time())).fetchone()
        if not row:
            return None
        return {"user": public_user(row), "csrf_token": row["csrf_token"], "token_hash": hashed}

    def create_session(self, db, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        # Limit concurrent sessions without retaining an unbounded login history.
        db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires_at DESC LIMIT 9)", (user_id, user_id))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, now + SESSION_SECONDS))
        db.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"user": public_user(user), "csrf_token": csrf}, token

    def setup(self, username, password):
        username = credentials(username, password)
        encoded = password_hash(password)
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise AuthError("管理员已初始化，请登录", 409)
            cursor = db.execute("INSERT INTO users (username,password_hash,role,status,created_at) VALUES (?,?,'admin','active',?)", (username, encoded, time.time()))
            return self.create_session(db, cursor.lastrowid)

    def register(self, username, password):
        username = credentials(username, password)
        encoded = password_hash(password)
        with self.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise AuthError("请先由管理员完成初始化", 409)
            if db.execute("SELECT value FROM settings WHERE key='registration_enabled'").fetchone()[0] != "true":
                raise AuthError("管理员已关闭注册", 403)
            self.insert_user(db, username, encoded, "viewer", "pending")
        return {"message": "注册申请已提交，管理员审核通过后即可登录"}

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 32 or len(password) > 128:
            raise AuthError("账号或密码错误", 401)
        with self.connection() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        valid = password_matches(password, row["password_hash"] if row else self.dummy_hash)
        if not row or not valid:
            raise AuthError("账号或密码错误", 401)
        with self.connection(write=True) as db:
            # Recheck state/hash under the same write transaction as session creation.
            fresh = db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            if not fresh or fresh["password_hash"] != row["password_hash"]:
                raise AuthError("账号或密码错误", 401)
            if fresh["status"] != "active":
                raise AuthError("账号正在等待管理员审核" if fresh["status"] == "pending" else "账号已被管理员停用", 403)
            return self.create_session(db, row["id"])

    def logout(self, session):
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (session["token_hash"],))

    @staticmethod
    def require_admin(db, actor_id):
        row = db.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
        if not row or row["role"] != "admin" or row["status"] != "active":
            raise AuthError("仅管理员可以管理用户", 403)

    @staticmethod
    def insert_user(db, username, encoded, role, status):
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 500:
            raise AuthError("用户数量已达到上限", 409)
        try:
            cursor = db.execute("INSERT INTO users (username,password_hash,role,status,created_at) VALUES (?,?,?,?,?)", (username, encoded, role, status, time.time()))
        except sqlite3.IntegrityError:
            raise AuthError("此账号已存在，请使用其他账号", 409)
        return public_user(db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone())

    def users(self, actor_id):
        with self.connection() as db:
            self.require_admin(db, actor_id)
            return {"users": [public_user(row) for row in db.execute("SELECT * FROM users ORDER BY created_at DESC")],
                    "registration_enabled": db.execute("SELECT value FROM settings WHERE key='registration_enabled'").fetchone()[0] == "true"}

    def create_user(self, actor_id, username, password, role="viewer"):
        username = credentials(username, password)
        if role not in ("admin", "viewer"):
            raise AuthError("无效的用户角色")
        encoded = password_hash(password)
        with self.connection(write=True) as db:
            self.require_admin(db, actor_id)
            return self.insert_user(db, username, encoded, role, "active")

    def update_user(self, actor_id, user_id, changes):
        if not changes or set(changes) - {"role", "status", "password"}:
            raise AuthError("只能修改角色、状态或密码")
        encoded = None
        if "password" in changes:
            validate_password(changes["password"])
            if actor_id == user_id:
                raise AuthError("请在账户安全中修改自己的密码")
            encoded = password_hash(changes["password"])
        with self.connection(write=True) as db:
            self.require_admin(db, actor_id)
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise AuthError("用户不存在", 404)
            role, status = changes.get("role", row["role"]), changes.get("status", row["status"])
            if role not in ("admin", "viewer") or status not in ("active", "pending", "disabled"):
                raise AuthError("无效的用户角色或状态")
            if actor_id == user_id and (role != "admin" or status != "active"):
                raise AuthError("不能停用或降低当前登录管理员的权限", 409)
            if row["role"] == "admin" and row["status"] == "active" and (role != "admin" or status != "active"):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0] <= 1:
                    raise AuthError("必须保留至少一名可登录的管理员", 409)
            db.execute("UPDATE users SET role=?,status=?,password_hash=? WHERE id=?", (role, status, encoded or row["password_hash"], user_id))
            if encoded or status != row["status"] or role != row["role"]:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return public_user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def delete_user(self, actor_id, user_id):
        with self.connection(write=True) as db:
            self.require_admin(db, actor_id)
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise AuthError("用户不存在", 404)
            if actor_id == user_id:
                raise AuthError("不能删除当前登录账号", 409)
            if row["role"] == "admin" and row["status"] == "active" and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0] <= 1:
                raise AuthError("必须保留至少一名可登录的管理员", 409)
            db.execute("DELETE FROM users WHERE id=?", (user_id,))

    def set_registration(self, actor_id, enabled):
        if not isinstance(enabled, bool):
            raise AuthError("registration_enabled 必须是布尔值")
        with self.connection(write=True) as db:
            self.require_admin(db, actor_id)
            db.execute("UPDATE settings SET value=? WHERE key='registration_enabled'", ("true" if enabled else "false",))
        return {"registration_enabled": enabled}

    def change_password(self, session, current_password, new_password):
        validate_password(new_password)
        if not isinstance(current_password, str) or len(current_password) > 128:
            raise AuthError("当前密码错误", 403)
        with self.connection() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (session["user"]["id"],)).fetchone()
        if not row or not password_matches(current_password, row["password_hash"]):
            raise AuthError("当前密码错误", 403)
        encoded = password_hash(new_password)
        with self.connection(write=True) as db:
            fresh = db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            valid_session = db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?", (session["token_hash"], time.time())).fetchone()
            if not fresh or fresh["status"] != "active" or fresh["password_hash"] != row["password_hash"] or not valid_session:
                raise AuthError("登录状态已失效，请重新登录", 401)
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            return self.create_session(db, row["id"])
