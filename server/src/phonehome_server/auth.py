"""Web logins: a couple of local accounts with long-lived sessions.

Passwords are hashed with scrypt (Python standard library). Sessions are
random tokens in an HttpOnly, SameSite=Lax cookie; only their SHA-256 is
stored, so a copy of the database can't be used to log in.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .db import Database, now_iso

COOKIE_NAME = "phonehome_session"
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                              p=int(p), dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(test.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, db: Database, session_days: int = 365) -> None:
        self.db = db
        self.session_s = session_days * 86400

    def set_user(self, username: str, password: str) -> None:
        username = username.strip()
        if not username or len(password) < 8:
            raise ValueError("username required and password must be at least 8 characters")
        with self.db.connect() as c:
            c.execute("INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(username) DO UPDATE SET pw_hash = excluded.pw_hash",
                      (username, hash_password(password), now_iso()))
            # Changing a password signs out that user's other sessions.
            c.execute("DELETE FROM sessions WHERE username = ?", (username,))

    def delete_user(self, username: str) -> bool:
        with self.db.connect() as c:
            return c.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0

    def users(self) -> list[str]:
        with self.db.connect() as c:
            return [r[0] for r in c.execute("SELECT username FROM users ORDER BY username")]

    def has_users(self) -> bool:
        with self.db.connect() as c:
            return c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def login(self, username: str, password: str) -> str | None:
        with self.db.connect() as c:
            row = c.execute("SELECT pw_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()
        # Always do the expensive hash, so timing doesn't reveal valid usernames.
        stored = row["pw_hash"] if row else hash_password("x" * 12)
        if not verify_password(password, stored) or not row:
            return None
        token = secrets.token_urlsafe(32)
        with self.db.connect() as c:
            c.execute("INSERT INTO sessions (token_hash, username, created_at, expires_ts) VALUES (?, ?, ?, ?)",
                      (_token_hash(token), username.strip(), now_iso(), time.time() + self.session_s))
        return token

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        with self.db.connect() as c:
            row = c.execute("SELECT username, expires_ts FROM sessions WHERE token_hash = ?",
                            (_token_hash(token),)).fetchone()
        if not row or row["expires_ts"] < time.time():
            return None
        return row["username"]

    def logout(self, token: str | None) -> None:
        if token:
            with self.db.connect() as c:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
