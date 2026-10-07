"""Validated environment configuration; storage is separated by trust level."""
import ipaddress
import os
import re
import secrets
import stat
from pathlib import Path
from urllib.parse import urlsplit


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise RuntimeError(f"{name} must be true or false")
    return value in {"true", "1", "yes", "on"}


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise RuntimeError(f"{name} must be between {low} and {high}")
    return value


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]


SITE_NAME = os.getenv("SITE_NAME", "Signage").strip()[:80] or "Signage"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "signage.db")))
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/media"))
ORIGINALS_DIR = Path(os.getenv("ORIGINALS_DIR", str(MEDIA_DIR / "originals")))
WORK_DIR = Path(os.getenv("WORK_DIR", str(MEDIA_DIR / "work")))
RENDERED_DIR = Path(os.getenv("RENDERED_DIR", str(MEDIA_DIR / "rendered")))
AUTH_MODE = [m.lower() for m in _list("AUTH_MODE")] or ["local"]
ALLOW_INSECURE_DEV = _bool("ALLOW_INSECURE_DEV", False)
SECRET_KEY = os.getenv("SECRET_KEY", "")
SESSION_HOURS = _int("SESSION_HOURS", 12, 1, 168)
# COOKIE_SECURE: "auto" (default) marks cookies Secure on HTTPS requests and still works on
# plain-HTTP LANs; "true" requires HTTPS for administration; "false" is for isolated testing.
_cookie_mode = os.getenv("COOKIE_SECURE", "auto").strip().lower()
COOKIE_AUTO = _cookie_mode == "auto"
COOKIE_SECURE = False if COOKIE_AUTO else _bool("COOKIE_SECURE", True)
# Without ALLOWED_HOSTS the portal accepts any IP-address Host header (DNS rebinding needs a
# hostname, so IP literals are safe) plus localhost and the hostnames added under Settings.
HOSTS_AUTO = not _list("ALLOWED_HOSTS")
ALLOWED_HOSTS = _list("ALLOWED_HOSTS") or ["localhost", "127.0.0.1", "::1"]
EXTRA_HOSTS: list[str] = []  # Settings → Access names (auto mode only)
# Optional one-time code the first-launch setup page asks for.
SETUP_TOKEN = os.getenv("SETUP_TOKEN", "").strip()
ADMIN_ORIGINS = _list("ADMIN_ORIGINS")
PLAYER_BASE_URL = os.getenv("PLAYER_BASE_URL", "").rstrip("/")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CF_TEAM_DOMAIN = os.getenv("CF_TEAM_DOMAIN", "")
CF_AUD = os.getenv("CF_AUD", "")
CF_ALLOWED_EMAILS = [e.lower() for e in _list("CF_ALLOWED_EMAILS")]
TARGET_WIDTH = _int("TARGET_WIDTH", 1920, 320, 3840)
TARGET_HEIGHT = _int("TARGET_HEIGHT", 1080, 240, 2160)
VIDEO_CRF = _int("VIDEO_CRF", 21, 14, 35)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 2048, 1, 8192)
MAX_INGEST_MB = _int("MAX_INGEST_MB", 8192, 1, 16384)
MAX_CONCURRENT_UPLOADS = _int("MAX_CONCURRENT_UPLOADS", 2, 1, 8)
BODY_IDLE_SECONDS = _int("BODY_IDLE_SECONDS", 60, 5, 300)
DEFAULT_SLIDE_SECONDS = _int("DEFAULT_SLIDE_SECONDS", 10, 2, 3600)
CLOCK_24H = _bool("CLOCK_24H", True)
PLAYER_POLL_SECONDS = _int("PLAYER_POLL_SECONDS", 5, 2, 60)
PLAYER_FADE_MS = _int("PLAYER_FADE_MS", 250, 0, 2000)
DISPLAY_ONLINE_SECONDS = _int("DISPLAY_ONLINE_SECONDS", 30, 10, 300)
INGEST_ENABLED = _bool("INGEST_ENABLED", True)
PLAYER_ONLY_HOSTS = _list("PLAYER_ONLY_HOSTS")
SOURCE_MEDIA_ORIGINS = _list("SOURCE_MEDIA_ORIGINS")
SOURCE_FRAME_ORIGINS = _list("SOURCE_FRAME_ORIGINS")
SHARES_CONFIG = os.getenv("SHARES_CONFIG", "")
TRANSFERS_ENABLED = _bool("TRANSFERS_ENABLED", True)
REQUIRE_MEDIA_MARKERS = _bool("REQUIRE_MEDIA_MARKERS", False)
MEDIA_VOLUME_ID = os.getenv("MEDIA_VOLUME_ID", "").strip()



def host_allowed(hostname: str | None) -> bool:
    """Host-header allowlist. Hostnames must be explicit; IP literals are accepted in auto mode."""
    if not hostname:
        return False
    if not HOSTS_AUTO:
        return "*" in ALLOWED_HOSTS or hostname in ALLOWED_HOSTS
    if hostname == "localhost" or hostname.lower() in EXTRA_HOSTS:
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def validate() -> None:
    if ("none" in AUTH_MODE or "*" in ALLOWED_HOSTS or not (COOKIE_SECURE or COOKIE_AUTO)) and not ALLOW_INSECURE_DEV:
        raise RuntimeError("Insecure settings require ALLOW_INSECURE_DEV=true. Use TLS and explicit hosts in production.")
    if "*" in _list("FORWARDED_ALLOW_IPS"):
        raise RuntimeError("FORWARDED_ALLOW_IPS must name trusted proxy addresses, never '*'.")
    if "none" in AUTH_MODE and AUTH_MODE != ["none"]:
        raise RuntimeError("AUTH_MODE=none cannot be combined with other providers.")
    if TARGET_WIDTH % 2 or TARGET_HEIGHT % 2:
        raise RuntimeError("Target width and height must be even for H.264.")
    if DISPLAY_ONLINE_SECONDS <= PLAYER_POLL_SECONDS * 2:
        raise RuntimeError("DISPLAY_ONLINE_SECONDS must exceed two player poll intervals.")
    for origin in ADMIN_ORIGINS + ([PLAYER_BASE_URL] if PLAYER_BASE_URL else []):
        u = urlsplit(origin)
        if u.scheme not in ("http", "https") or not u.netloc or u.path or u.query or u.fragment or u.username:
            raise RuntimeError("ADMIN_ORIGINS and PLAYER_BASE_URL must be origins only, e.g. https://signage.example.org")
    for origin in SOURCE_MEDIA_ORIGINS + SOURCE_FRAME_ORIGINS:
        u = urlsplit(origin)
        if (u.scheme not in ("https", "http") or not u.hostname or u.path or u.query or u.fragment
                or u.username or u.password or any(c in origin for c in "*;\\ ")
                or any(ord(c) < 32 or ord(c) > 126 for c in origin)):
            raise RuntimeError("Source allowlists must contain exact HTTP(S) origins, without paths or wildcards.")
        if u.scheme != "https" and not ALLOW_INSECURE_DEV:
            raise RuntimeError("External sources require HTTPS in production.")
        private_hosts = {urlsplit(o).hostname for o in ADMIN_ORIGINS + ([PLAYER_BASE_URL] if PLAYER_BASE_URL else [])}
        if u.hostname in private_hosts:
            # Cookies are hostname-scoped, not port-scoped.
            raise RuntimeError("Serve external content from a different hostname, not an admin/player hostname on another port.")
    if (SOURCE_MEDIA_ORIGINS or SOURCE_FRAME_ORIGINS) and not ALLOW_INSECURE_DEV:
        admin_hosts = {urlsplit(o).hostname for o in ADMIN_ORIGINS}
        if not PLAYER_BASE_URL or not admin_hosts or urlsplit(PLAYER_BASE_URL).hostname in admin_hosts:
            raise RuntimeError("External sources require a separate playback hostname and explicit ADMIN_ORIGINS.")
    for host in PLAYER_ONLY_HOSTS:
        if not re.fullmatch(r"[A-Za-z0-9.:-]+", host):
            raise RuntimeError("PLAYER_ONLY_HOSTS must contain hostnames, not URLs.")
    if REQUIRE_MEDIA_MARKERS and not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", MEDIA_VOLUME_ID):
        raise RuntimeError("Set MEDIA_VOLUME_ID to the persistent NAS marker ID.")
    if SETUP_TOKEN and not 8 <= len(SETUP_TOKEN) <= 128:
        raise RuntimeError("SETUP_TOKEN must be 8 to 128 characters.")
    if SECRET_KEY and len(SECRET_KEY) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters.")


def ensure_dirs() -> None:
    os.umask(0o027)
    if REQUIRE_MEDIA_MARKERS:
        # Never create an empty local replacement for an absent NAS volume.
        # The systemd host preflight also checks the actual mount source/type.
        from .storage_guard import verify_media
        verify_media()
    for d in (DATA_DIR, ORIGINALS_DIR, WORK_DIR, RENDERED_DIR):
        d.mkdir(parents=True, exist_ok=True)
        if d.is_symlink() or not d.is_dir():
            raise RuntimeError(f"Storage directory is not a real directory: {d}")
    DATA_DIR.chmod(0o700)


def secret_key() -> str:
    global SECRET_KEY
    if SECRET_KEY:
        return SECRET_KEY
    keyfile = DATA_DIR / ".secret_key"
    try:
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(keyfile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or not 32 <= st.st_size <= 4096:
                raise RuntimeError("Invalid secret-key file.")
            SECRET_KEY = fh.read(4096).strip()
    else:
        SECRET_KEY = secrets.token_urlsafe(48)
        with os.fdopen(fd, "w") as fh:
            fh.write(SECRET_KEY)
            fh.flush()
            os.fsync(fh.fileno())
    if len(SECRET_KEY) < 32:
        raise RuntimeError("Invalid secret key.")
    return SECRET_KEY
