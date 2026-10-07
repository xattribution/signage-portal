"""Local accounts: Argon2id passwords, hashed opaque sessions, persistent throttling."""
import hashlib
import hmac
import re
import secrets
import threading

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import config, db
from ..security import check_write
from .base import AuthProvider, Principal

COOKIE = "__Host-sp_session" if config.COOKIE_SECURE else "sp_session"
SECURE_COOKIE = "__Host-sp_session"


def _secure(request: Request) -> bool:
    """Strict mode always sets Secure; auto mode follows the request's (proxy-resolved) scheme."""
    return config.COOKIE_SECURE or (config.COOKIE_AUTO and request.url.scheme == "https")


def cookie_name(request: Request) -> str:
    return SECURE_COOKIE if _secure(request) else "sp_session"
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
_PASSWORD_WORK = threading.BoundedSemaphore(2)
_WINDOW_MS = 600_000
_ATTEMPT_LIMIT = 8


def hash_password(pw: str) -> str:
    with _PASSWORD_WORK:
        return HASHER.hash(pw)


def check_password(pw: str, hashed: str) -> bool:
    if hashed.startswith(("$2a$", "$2b$", "$2y$")):
        # Compatibility only. A successful legacy login is upgraded to Argon2id.
        # Never truncate long passwords; bcrypt only accepts up to 72 bytes.
        if len(pw.encode()) > 72:
            return False
        import bcrypt
        try:
            return bcrypt.checkpw(pw.encode(), hashed.encode())
        except ValueError:
            return False
    try:
        return HASHER.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def validate_new_password(pw: str) -> None:
    if len(pw) < 12:
        raise HTTPException(400, "Use at least 12 characters.")
    if len(pw) > 128 or len(pw.encode()) > 512:
        raise HTTPException(400, "Use no more than 128 characters or 512 UTF-8 bytes.")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _reserve_attempt(username: str, ip: str) -> None:
    now = db.now_ms()
    buckets = [hmac.new(config.secret_key().encode(), v.encode(), hashlib.sha256).hexdigest()
               for v in ("user:" + username.lower(), "ip:" + ip)]
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM auth_attempts WHERE expires_at<=?", (now,))
        c.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        for bucket in buckets:
            row = c.execute("SELECT * FROM auth_attempts WHERE bucket=?", (bucket,)).fetchone()
            if row and row["attempts"] >= _ATTEMPT_LIMIT:
                retry = max(1, (row["expires_at"] - now + 999) // 1000)
                raise HTTPException(429, "Too many sign-in attempts. Try again later.", {"Retry-After": str(retry)})
        if c.execute("SELECT COUNT(*) FROM auth_attempts").fetchone()[0] >= 4096:
            raise HTTPException(429, "Sign-in capacity is busy. Try again later.", {"Retry-After": "60"})
        for bucket in buckets:
            c.execute("INSERT INTO auth_attempts(bucket, expires_at, attempts) VALUES (?,?,1) "
                      "ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1", (bucket, now + _WINDOW_MS))


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=1, max_length=128)


class LocalAuth(AuthProvider):
    name = "local"
    interactive = True
    manages_users = True

    def __init__(self) -> None:
        self._dummy_hash = hash_password(secrets.token_urlsafe(32))
        self._bootstrap()

    def _bootstrap(self) -> None:
        if db.row("SELECT id FROM users LIMIT 1"):
            return
        if not config.ADMIN_PASSWORD:
            # The first-launch setup page creates the first account in the browser.
            print("[portal] No accounts yet. Open the portal in a browser to finish first-launch setup.", flush=True)
            return
        validate_new_password(config.ADMIN_PASSWORD)
        if not re.fullmatch(r"[A-Za-z0-9._@-]{2,64}", config.ADMIN_USER):
            raise RuntimeError("Invalid ADMIN_USER.")
        db.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
                   (config.ADMIN_USER, hash_password(config.ADMIN_PASSWORD), db.now_ms()))
        db.audit("system", f"created bootstrap account '{config.ADMIN_USER}'")

    def _issue(self, response: Response, user: dict, request: Request | None = None) -> None:
        token = secrets.token_urlsafe(32)
        now = db.now_ms()
        with db.tx() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT session_gen, password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not current or current["session_gen"] != user["session_gen"] or current["password_hash"] != user["password_hash"]:
                raise HTTPException(401, "Account changed. Sign in again.")
            c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                      (_digest(token), user["id"], user["session_gen"], now, now + config.SESSION_HOURS * 3600000))
            c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN "
                      "(SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 20)",
                      (user["id"], user["id"]))
        secure = _secure(request) if request is not None else config.COOKIE_SECURE
        response.set_cookie(SECURE_COOKIE if secure else "sp_session", token, max_age=config.SESSION_HOURS * 3600,
                            httponly=True, secure=secure, samesite="strict", path="/")

    def identify(self, request: Request) -> Principal | None:
        token = request.cookies.get(cookie_name(request), "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        user = db.row("SELECT u.username FROM sessions s JOIN users u ON u.id=s.user_id "
                      "WHERE s.token_hash=? AND s.expires_at>? AND s.generation=u.session_gen",
                      (_digest(token), db.now_ms()))
        return Principal(username=user["username"], provider=self.name) if user else None

    def login_url(self, request: Request) -> str:
        return "/login"

    def logout(self, request: Request, response: Response) -> None:
        token = request.cookies.get(cookie_name(request), "")
        if token:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_digest(token),))
        secure = _secure(request)
        response.delete_cookie(cookie_name(request), path="/", secure=secure, httponly=True, samesite="strict")
        if secure:
            response.delete_cookie("sp_session", path="/")  # Remove plain-HTTP and pre-upgrade cookies.

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.post("/auth/login")
        def login(body: LoginBody, request: Request, response: Response):
            check_write(request)
            # A local login cannot bypass another provider in the configured chain.
            from . import chain
            for provider in chain:
                if provider is not self and provider.identify(request) is None:
                    raise HTTPException(401, "External authentication is required.")
            ip = request.client.host if request.client else "unknown"
            _reserve_attempt(body.username, ip)
            if not _PASSWORD_WORK.acquire(blocking=False):
                raise HTTPException(429, "Sign-in capacity is busy. Try again shortly.", {"Retry-After": "2"})
            try:
                user = db.row("SELECT * FROM users WHERE username=?", (body.username,))
                valid = check_password(body.password, user["password_hash"] if user else self._dummy_hash)
                if not valid or not user:
                    db.audit(body.username, f"failed login from {ip[:64]}")
                    raise HTTPException(401, "Invalid username or password.")
                if user["password_hash"].startswith("$2") or HASHER.check_needs_rehash(user["password_hash"]):
                    new_hash = HASHER.hash(body.password)
                    db.execute("UPDATE users SET password_hash=? WHERE id=? AND password_hash=?",
                               (new_hash, user["id"], user["password_hash"]))
                    user["password_hash"] = new_hash
            finally:
                _PASSWORD_WORK.release()
            self._issue(response, user, request)
            db.audit(user["username"], f"login from {ip[:64]}")
            return {"ok": True}

        return r
