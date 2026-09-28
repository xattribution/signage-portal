"""Approved browser sources, never a general-purpose server-side URL fetcher."""
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException

from . import config, db
from .auth import CurrentUser
from .auth.base import Principal
from .schemas import FeedBody

router = APIRouter()
KINDS = {"hls", "remote_video", "remote_image", "web"}
THUMB = "/static/feed.svg"


def validate_url(value: str, kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(400, "Unsupported source type.")
    if len(value) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value) or "\\" in value:
        raise HTTPException(400, "Use a valid URL without spaces or control characters.")
    try:
        u = urlsplit(value)
        if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError()
        port = u.port
        origin = f"{u.scheme}://{u.hostname.lower()}"
        if ":" in u.hostname:
            origin = f"{u.scheme}://[{u.hostname.lower()}]"
        if port and port != (443 if u.scheme == "https" else 80):
            origin += f":{port}"
        if u.hostname.lower() in {"localhost", "metadata.google.internal"} or u.hostname.endswith(".localhost"):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(u.hostname)
        except ValueError:
            ip = None
        if ip and (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved):
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Use a direct HTTP(S) source URL without credentials or a fragment.") from None
    allowed = config.SOURCE_FRAME_ORIGINS if kind == "web" else config.SOURCE_MEDIA_ORIGINS
    if origin not in allowed:
        raise HTTPException(400, "This source origin is not approved. Add it to the server's source allowlist first.")
    private_hosts = {urlsplit(o).hostname for o in config.ADMIN_ORIGINS + ([config.PLAYER_BASE_URL] if config.PLAYER_BASE_URL else [])}
    if u.hostname in private_hosts:
        raise HTTPException(400, "External content must use a different hostname from administration and playback, not just another port.")
    if u.scheme != "https" and not config.ALLOW_INSECURE_DEV:
        raise HTTPException(400, "Use HTTPS for external content.")
    # Do not fetch this URL here, resolve DNS, follow redirects, or copy a token to a log.
    return urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, ""))


def public_source(slide: dict) -> dict | None:
    try:
        url = validate_url(slide["file"], slide["kind"])
    except HTTPException:
        return None  # Removing an origin also revokes already-saved sources.
    return {"url": url, "thumb": THUMB}


@router.post("/api/feeds", status_code=201)
def create_feed(body: FeedBody, me: Principal = CurrentUser):
    url = validate_url(body.url, body.kind)
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        uid = db.allocate_id(c, "uploads")
        c.execute("INSERT INTO uploads(id,title,original_name,original_file,kind,status,created_by,created_at,source_url,feed_kind) "
                  "VALUES (?,?,'','','feed','ready',?,?,?,?)",
                  (uid, body.title, me.username, db.now_ms(), url, body.kind))
        c.execute("INSERT INTO slides(upload_id,idx,kind,file,thumb,duration_ms) VALUES (?,0,?,?,?,NULL)",
                  (uid, body.kind, url, THUMB))
        c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)",
                  (db.now_ms(), me.username, f"added external source '{body.title}' ({body.kind})"))
    return {"id": uid, "kind": "feed"}


@router.put("/api/feeds/{uid}")
def update_feed(uid: int, body: FeedBody, me: Principal = CurrentUser):
    url = validate_url(body.url, body.kind)
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT id FROM uploads WHERE id=? AND kind='feed'", (uid,)).fetchone():
            raise HTTPException(404, "No such external source.")
        c.execute("UPDATE uploads SET title=?,source_url=?,feed_kind=? WHERE id=?",
                  (body.title, url, body.kind, uid))
        c.execute("UPDATE slides SET kind=?,file=? WHERE upload_id=?", (body.kind, url, uid))
        c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)",
                  (db.now_ms(), me.username, f"updated external source '{body.title}'"))
    return {"ok": True}
