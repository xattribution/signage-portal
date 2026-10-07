import hashlib
import hmac
import ipaddress
import uuid
import json
import sqlite3
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .schemas import (StreamBody, StreamPatch, OrderBody, DisplayBody, UploadPatch, PlacementBody, PlacementPatch, NewUser, NewPassword, SetupBody)
from .security import BoundaryMiddleware

from . import auth, config, db, jobs, playlist, feeds, shares, presentation, settings
from .auth import CurrentUser
from .auth.base import Principal
from .auth.local import hash_password, validate_new_password

STATIC = Path(__file__).parent / "static"

db.init()
auth.init()


@asynccontextmanager
async def lifespan(_app):
    shares.load_config()
    shares.start()
    jobs.start()
    print(f"[portal] auth chain: {', '.join(config.AUTH_MODE)} | media: {config.MEDIA_DIR}", flush=True)
    try:
        yield
    finally:
        jobs.stop()
        shares.stop()


app = FastAPI(title="Signage", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

# Provider-owned routes (login post, future OAuth callbacks, ...)
for _p in auth.chain:
    _r = _p.router()
    if _r is not None:
        app.include_router(_r)


app.add_middleware(BoundaryMiddleware)
app.include_router(feeds.router)
app.include_router(shares.router)
app.include_router(presentation.router)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    # Do not echo passwords, upload metadata or rejected request bodies in errors.
    detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse({"detail": detail}, status_code=422)


@app.exception_handler(sqlite3.IntegrityError)
async def constraint_error(_request, _exc):
    return JSONResponse({"detail": "This conflicts with an existing record. Refresh and try again."}, status_code=409)


@app.exception_handler(OverflowError)
async def out_of_range(_request, _exc):
    return JSONResponse({"detail": "Numeric value is out of range."}, status_code=400)


MEDIA_PATH = re.compile(r"[0-9]{1,12}/(?:[0-9a-f]{32}/)?[0-9]{3}(_t)?\.(jpg|mp4|png|gif)")


class ServedMedia(StaticFiles):
    """
    Serve only the narrow media path contract, including legacy filenames.
    Forced MIME types, nosniff and a sandbox CSP constrain active web content;
    they do not prove that media is harmless to every decoder.
    """
    async def get_response(self, path, scope):
        if not MEDIA_PATH.fullmatch(path):
            return Response(status_code=404)
        resp = await super().get_response(path, scope)
        if resp.status_code in (200, 206):
            resp.headers["Content-Type"] = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}[Path(path).suffix]
            resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                               if len(path.split("/")) == 3 else "public, max-age=3600")
            resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            resp.headers["Content-Disposition"] = "inline"
        return resp


config.ensure_dirs()
app.mount("/media", ServedMedia(directory=config.RENDERED_DIR, check_dir=False, follow_symlink=False), name="media")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def _page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})


def _local_provider():
    return next((p for p in auth.chain if p.name == "local"), None)


def setup_needed() -> bool:
    """First launch: local accounts are in use and none exist yet."""
    return _local_provider() is not None and not db.row("SELECT id FROM users LIMIT 1")


@app.get("/", include_in_schema=False)
def admin_page(request: Request):
    if setup_needed():
        return RedirectResponse("/setup", status_code=303)
    who, failed = auth.identify(request)
    if who is None:
        url = failed.login_url(request) if failed else None
        if url:
            return RedirectResponse(url, status_code=303)
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return _page("admin.html")


@app.get("/login", include_in_schema=False)
def login_page():
    if setup_needed():
        return RedirectResponse("/setup", status_code=303)
    if not any(p.interactive for p in auth.chain):
        return RedirectResponse("/", status_code=303)
    return _page("login.html")


# ---------------------------------------------------------------------------
# First-launch setup. Open only while no account exists, only to LAN/loopback
# clients, and (optionally) only with SETUP_TOKEN.
# ---------------------------------------------------------------------------
def _lan_client(request: Request) -> bool:
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


@app.get("/setup", include_in_schema=False)
def setup_page():
    if not setup_needed():
        return RedirectResponse("/", status_code=303)
    return _page("setup.html")


@app.get("/api/setup", include_in_schema=False)
def setup_status(request: Request):
    return {"needed": setup_needed(), "code_required": bool(config.SETUP_TOKEN),
            "lan": _lan_client(request), "site_name": config.SITE_NAME}


@app.post("/api/setup", include_in_schema=False)
def setup_finish(body: SetupBody, request: Request, response: Response):
    local = _local_provider()
    if local is None or not setup_needed():
        raise HTTPException(409, "Setup is already complete. Sign in instead.")
    if not _lan_client(request):
        raise HTTPException(403, "Finish setup from a computer on the same network as the server.")
    if config.SETUP_TOKEN:
        from .auth.local import _reserve_attempt
        _reserve_attempt("setup", request.client.host if request.client else "unknown")
        if not hmac.compare_digest(body.setup_code.strip().encode(), config.SETUP_TOKEN.encode()):
            raise HTTPException(401, "That setup code is not correct.")
    validate_new_password(body.password)
    changes = {"site_name": body.site_name} if not settings.locked("site_name") else {}
    password_hash = hash_password(body.password)
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "Setup is already complete. Sign in instead.")
        c.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
                  (body.username, password_hash, db.now_ms()))
    if changes:
        settings.save(changes)
    user = db.row("SELECT * FROM users WHERE username=?", (body.username,))
    local._issue(response, user, request)
    ip = request.client.host if request.client else "unknown"
    db.audit(body.username, f"completed first-launch setup from {ip[:64]}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings(me: Principal = CurrentUser):
    return settings.current()


@app.patch("/api/settings")
async def patch_settings(request: Request, me: Principal = CurrentUser):
    try:
        changes = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Send the settings as JSON.") from exc
    changed = await run_in_threadpool(settings.save, changes)
    if changed:
        db.audit(me.username, "changed settings: " + ", ".join(changed))
    return settings.current()


@app.post("/auth/logout", include_in_schema=False)
def logout(request: Request):
    redirect = "/cdn-cgi/access/logout" if any(p.name == "cloudflare" for p in auth.chain) else "/login"
    resp = JSONResponse({"ok": True, "redirect": redirect})
    who, _ = auth.identify(request)
    for provider in auth.chain:
        provider.logout(request, resp)
    if who:
        db.audit(who.username, "signed out")
    return resp


@app.get("/logout", include_in_schema=False)
def old_logout():
    raise HTTPException(405, "Sign out with POST /auth/logout.", headers={"Allow": "POST"})


def _player_page(request: Request):
    if config.PLAYER_BASE_URL and str(request.base_url).rstrip("/") != config.PLAYER_BASE_URL:
        sound = "?sound=1" if request.query_params.get("sound") == "1" else ""
        return RedirectResponse(config.PLAYER_BASE_URL + request.url.path + sound, status_code=307)
    return _page("player.html")


@app.get("/display/{slug}", include_in_schema=False)
def display_page(slug: str, request: Request):
    return _player_page(request)


@app.get("/stream/{key}", include_in_schema=False)
def stream_page(key: str, request: Request):
    return _player_page(request)


@app.get("/preview/{stream_id}", include_in_schema=False)
def preview_page(stream_id: int, request: Request):
    return _player_page(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/api/site", include_in_schema=False)
def site():
    return {"name": config.SITE_NAME, "setup_needed": setup_needed()}


# ---------------------------------------------------------------------------
# Player API (no auth: displays only ever read public content)
# ---------------------------------------------------------------------------
def _player_payload(state: dict) -> dict:
    return {
        **state,
        "server_time_ms": db.now_ms(),
        "poll_seconds": config.PLAYER_POLL_SECONDS,
        "fade_ms": config.PLAYER_FADE_MS,
        "clock_24h": config.CLOCK_24H,
        "media_origins": config.SOURCE_MEDIA_ORIGINS,
        "frame_origins": config.SOURCE_FRAME_ORIGINS,
    }


@app.get("/api/player/channel/{key}")
def player_channel(key: str):
    stream = db.row("SELECT id FROM streams WHERE playback_key=?", (key,))
    if not stream:
        raise HTTPException(404, "This stream is not available.")
    return _player_payload(playlist.stream_state(stream["id"]))


@app.get("/api/player/display/{slug}")
def player_display(slug: str, request: Request, now: int | None = Query(default=None, ge=0, le=9223372036854775807)):
    d = db.row("SELECT * FROM displays WHERE slug=?", (slug,))
    if not d:
        return JSONResponse({"unknown_display": slug, "server_time_ms": db.now_ms(),
                             "poll_seconds": config.PLAYER_POLL_SECONDS}, status_code=404)
    ip = request.client.host if request.client else ""  # proxy-resolved only for trusted proxies
    db.execute("UPDATE displays SET last_seen=?, last_ip=?, now_slide=? WHERE id=?",
               (db.now_ms(), ip[:64], now, d["id"]))
    payload = _player_payload(playlist.stream_state(d["stream_id"]))
    payload["display"] = {"slug": d["slug"], "name": d["name"]}
    return payload


@app.get("/api/player/stream/{stream_id}")
def player_stream(stream_id: int):
    if not db.row("SELECT id FROM streams WHERE id=?", (stream_id,)):
        raise HTTPException(404, "No such stream")
    return _player_payload(playlist.stream_state(stream_id))


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------
@app.get("/api/overview")
def overview(request: Request, me: Principal = CurrentUser):
    now = db.now_ms()
    with db.tx() as c:
        c.execute("BEGIN")
        streams = [dict(r) for r in c.execute("SELECT * FROM streams ORDER BY kind='screensaver', id")]
        uploads = [dict(r) for r in c.execute("SELECT * FROM uploads ORDER BY created_at DESC, id DESC")]
        slides = [dict(r) for r in c.execute("SELECT * FROM slides ORDER BY upload_id,idx")]
        placements = [dict(r) for r in c.execute("SELECT * FROM placements ORDER BY position,id")]
        displays = [dict(r) for r in c.execute("SELECT * FROM displays ORDER BY name COLLATE NOCASE")]
    snapshot = playlist.Snapshot(streams, placements, slides, uploads)
    by_up: dict[int, list] = {}
    for s in slides:
        by_up.setdefault(s["upload_id"], []).append({**s, "thumb": feeds.THUMB if s["kind"] in feeds.KINDS else f"/media/{s['thumb']}"})
    for u in uploads:
        all_slides = by_up.get(u["id"], [])
        u["slide_count"] = len(all_slides)
        u["slides"] = all_slides[:24]
        u.pop("original_file", None)
        u.pop("job_token", None)
        u.pop("transfer_key", None)

    states = {s["id"]: snapshot.state(s["id"], now) for s in streams}
    for s in streams:
        st = states[s["id"]]
        cur = playlist.position(st, now)
        s.pop('presentation_json', None)
        s["player_path"] = f"/stream/{s['playback_key']}"
        s["mode"] = st["mode"]
        s["source"] = st["source"]
        s["now_thumb"] = cur["thumb"] if cur else None
        s["now_title"] = cur["title"] if cur else None
        s["next_change_ms"] = st["next_change_ms"]
        s["loop_ms"] = sum(i["duration_ms"] for i in st["items"])
        s["item_count"] = len(st["items"])

    slide_thumb = {s["id"]: feeds.THUMB if s["kind"] in feeds.KINDS else f"/media/{s['thumb']}" for s in slides}
    for d in displays:
        d["online"] = bool(d["last_seen"] and now - d["last_seen"] < config.DISPLAY_ONLINE_SECONDS * 1000)
        d["now_thumb"] = slide_thumb.get(d["now_slide"]) if d["online"] else None

    payload = {
        "me": {"username": me.username, "provider": me.provider},
        "site_name": config.SITE_NAME,
        "player_base_url": config.PLAYER_BASE_URL,
        "worker_alive": jobs.worker_alive(),
        "clock_24h": config.CLOCK_24H,
        "manages_users": auth.manages_users(),
        "server_time_ms": now,
        "default_slide_seconds": config.DEFAULT_SLIDE_SECONDS,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "accept": sorted(jobs.ALLOWED_EXT),
        "source_media_origins": config.SOURCE_MEDIA_ORIGINS,
        "source_frame_origins": config.SOURCE_FRAME_ORIGINS,
        "shares_configured": bool(config.SHARES_CONFIG),
        "streams": streams,
        "displays": displays,
        "uploads": uploads,
        "placements": placements,
    }

    signature = {**payload, "server_time_ms": None,
                 "displays": [{**d, "last_seen": None if d["online"] else d["last_seen"]} for d in displays]}
    etag = '"' + hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Cookie, Cf-Access-Jwt-Assertion",
               "X-Server-Time": str(now)}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)


# ---- Streams ---------------------------------------------------------------


@app.post("/api/streams")
def create_stream(body: StreamBody, me: Principal = CurrentUser):
    key = uuid.uuid4().hex
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        sid = db.allocate_id(c, "streams")
        c.execute("INSERT INTO streams(id,name,created_at,playback_key) VALUES (?,?,?,?)",
                  (sid,body.name.strip(),db.now_ms(),key))
    db.audit(me.username, f"created stream '{body.name.strip()}'")
    return {"id": sid, "player_path": f"/stream/{key}"}


@app.patch("/api/streams/{sid}")
def update_stream(sid: int, body: StreamPatch, me: Principal = CurrentUser):
    old = _get("streams", sid)
    if body.name is not None and body.name.strip() != old["name"]:
        db.execute("UPDATE streams SET name=? WHERE id=?", (body.name.strip(), sid))
        db.audit(me.username, f"renamed stream '{old['name']}' to '{body.name.strip()}'")
    if body.fallback is not None and old["kind"] == "normal":
        db.execute("UPDATE streams SET fallback=? WHERE id=?", (body.fallback, sid))
        db.audit(me.username, f"set '{old['name']}' to show {body.fallback} when empty")
    return {"ok": True}


@app.delete("/api/streams/{sid}")
def delete_stream(sid: int, me: Principal = CurrentUser):
    s = _get("streams", sid)
    if s["kind"] == "screensaver":
        raise HTTPException(400, "The screensaver stream can't be deleted.")
    db.execute("DELETE FROM streams WHERE id=?", (sid,))
    db.audit(me.username, f"deleted stream '{s['name']}'")
    return {"ok": True}


@app.post("/api/streams/{sid}/order")
def reorder(sid: int, body: OrderBody, me: Principal = CurrentUser):
    _get("streams", sid)
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        current = {r[0] for r in c.execute("SELECT id FROM placements WHERE stream_id=? AND mode='rotation'", (sid,))}
        if len(body.placement_ids) != len(set(body.placement_ids)) or set(body.placement_ids) != current:
            raise HTTPException(409, "The rotation changed. Refresh before reordering.")
        for pos, pid in enumerate(body.placement_ids):
            c.execute("UPDATE placements SET position=? WHERE id=?", (pos, pid))
        c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)", (db.now_ms(), me.username, f"reordered stream {sid}"))
    return {"ok": True}


# ---- Displays --------------------------------------------------------------
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@app.post("/api/displays")
def create_display(body: DisplayBody, me: Principal = CurrentUser):
    slug = (body.slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "Display ID: lowercase letters, numbers and dashes only.")
    if db.row("SELECT id FROM displays WHERE slug=?", (slug,)):
        raise HTTPException(409, "That display ID is already in use.")
    if body.stream_id is not None:
        _get("streams", body.stream_id)
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT slug FROM retired_display_slugs WHERE slug=?", (slug,)).fetchone():
            raise HTTPException(409, "That retired display URL cannot be reused. Choose a new ID.")
        did = db.allocate_id(c, "displays")
        c.execute("INSERT INTO displays(id,slug,name,stream_id,created_at) VALUES (?,?,?,?,?)",
                  (did,slug,(body.name or slug).strip(),body.stream_id,db.now_ms()))
    db.audit(me.username, f"added display '{slug}'")
    return {"id": did}


@app.patch("/api/displays/{did}")
def update_display(did: int, body: DisplayBody, me: Principal = CurrentUser):
    d = _get("displays", did)
    fields = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.slug is not None:
        slug = body.slug.strip().lower()
        if not SLUG_RE.fullmatch(slug):
            raise HTTPException(400, "Use lowercase letters, numbers and dashes for the display ID.")
        if db.row("SELECT id FROM displays WHERE slug=? AND id<>?", (slug, did)):
            raise HTTPException(409, "That display ID is already in use.")
        if slug != d["slug"]:
            raise HTTPException(409, "Display URLs are permanent. Rename the display without changing its ID.")
    if body.stream_id is not None or body.clear_stream:
        sid = None if body.clear_stream else body.stream_id
        if sid is not None:
            _get("streams", sid)
        fields["stream_id"] = sid
    if fields:
        sets = ", ".join(f"{key}=?" for key in fields)
        db.execute(f"UPDATE displays SET {sets} WHERE id=?", (*fields.values(), did))
        db.audit(me.username, f"updated display '{d['name']}': {', '.join(fields)}")
    return {"ok": True}


@app.delete("/api/displays/{did}")
def delete_display(did: int, me: Principal = CurrentUser):
    d = _get("displays", did)
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR IGNORE INTO retired_display_slugs(slug) VALUES (?)", (d["slug"],))
        c.execute("DELETE FROM displays WHERE id=?", (did,))
    db.audit(me.username, f"removed display '{d['slug']}'")
    return {"ok": True}


# ---- Uploads ---------------------------------------------------------------
@app.post("/api/graphics", status_code=201)
@app.post("/api/uploads", status_code=201)
async def upload(request: Request, me: Principal = CurrentUser):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(415, "Use multipart/form-data.")
    async with request.form(max_files=1, max_fields=1, max_part_size=8192) as form:
        file = form.get("file")
        title = form.get("title", "")
        if not isinstance(file, UploadFile) or not isinstance(title, str) or set(form) - {"file", "title"}:
            raise HTTPException(400, "Send one file and an optional title.")
        if len(title) > 120:
            raise HTTPException(400, "Title must be at most 120 characters.")
        if file.size is not None and file.size > config.MAX_UPLOAD_MB * 1048576:
            raise HTTPException(413, "File is too large.")
        return await run_in_threadpool(jobs.store_upload, file.file, file.filename or "upload", title, me.username,
                                       graphic=request.url.path == '/api/graphics')


@app.patch("/api/uploads/{uid}")
def rename_upload(uid: int, body: UploadPatch, me: Principal = CurrentUser):
    _get("uploads", uid)
    db.execute("UPDATE uploads SET title=? WHERE id=?", (body.title.strip(), uid))
    db.audit(me.username, f"renamed upload {uid} to '{body.title}'")
    return {"ok": True}


@app.delete("/api/uploads/{uid}")
def delete_upload(uid: int, me: Principal = CurrentUser):
    u = _get("uploads", uid)
    with db.tx() as conn:
        conn.execute('BEGIN IMMEDIATE')
        used = presentation.references(conn, uid)
        if used:
            raise HTTPException(409, 'Remove this graphic from ' + ', '.join(used[:6]) + ' before deleting it.')
        conn.execute("DELETE FROM uploads WHERE id=?", (uid,))
    if u["kind"] != "feed":
        jobs.delete_files(u)
    db.audit(me.username, f"deleted '{u['title']}'")
    return {"ok": True}


@app.post("/api/uploads/{uid}/retry")
def retry_upload(uid: int, me: Principal = CurrentUser):
    with jobs._lock(uid):
        up = _get("uploads", uid)
        if up["kind"] == "feed":
            raise HTTPException(400, "External sources are not upload conversions.")
        if up["status"] != "error":
            raise HTTPException(409, "Only failed conversions can be retried.")
        db.execute("UPDATE uploads SET status='processing', error=NULL WHERE id=?", (uid,))
        jobs.submit(uid)
        db.audit(me.username, f"retried '{up['title']}'")
    return {"ok": True}


# ---- Placements ------------------------------------------------------------


@app.post("/api/placements")
def create_placement(body: PlacementBody, me: Principal = CurrentUser):
    u = _get("uploads", body.upload_id)
    if body.end_at is not None and body.start_at is not None and body.end_at <= body.start_at:
        raise HTTPException(400, "End time must be after the start time.")
    if body.mode == "override":
        if not body.end_at:
            raise HTTPException(400, "Overrides need an end time.")
        if body.end_at <= db.now_ms():
            raise HTTPException(400, "End time is already in the past.")

    if body.all_streams:
        targets = [None] if body.mode == "override" else [
            s["id"] for s in db.rows("SELECT id FROM streams WHERE kind='normal'")]
    else:
        targets = sorted(set(body.stream_ids))
        for sid in targets:
            _get("streams", sid)
    if not targets:
        raise HTTPException(400, "Pick at least one stream.")

    now = db.now_ms()
    ids = []
    with db.tx() as c:
        for sid in targets:
            pos = c.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM placements WHERE stream_id IS ?", (sid,)
            ).fetchone()[0]
            cur = c.execute(
                "INSERT INTO placements(upload_id, stream_id, mode, position, slide_seconds, start_at, end_at, "
                "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (body.upload_id, sid, body.mode, pos, body.slide_seconds, body.start_at, body.end_at,
                 me.username, now),
            )
            ids.append(cur.lastrowid)
    where = "all streams" if body.all_streams else ", ".join(
        r["name"] for r in db.rows(f"SELECT name FROM streams WHERE id IN ({','.join('?' * len(targets))})", targets)
    )
    db.audit(me.username, f"put '{u['title']}' on {where} ({body.mode})")
    return {"ids": ids}


@app.patch("/api/placements/{pid}")
def update_placement(pid: int, body: PlacementPatch, me: Principal = CurrentUser):
    p = _get("placements", pid)
    fields = {}
    if body.slide_seconds is not None:
        fields["slide_seconds"] = body.slide_seconds
    if body.start_at is not None or body.clear_start:
        fields["start_at"] = None if body.clear_start else body.start_at
    if body.end_at is not None or body.clear_end:
        if p["mode"] == "override" and body.clear_end:
            raise HTTPException(400, "Overrides need an end time.")
        fields["end_at"] = None if body.clear_end else body.end_at
    if body.enabled is not None:
        fields["enabled"] = 1 if body.enabled else 0
    merged = {**p, **fields}
    if merged["end_at"] is not None and merged["start_at"] is not None and merged["end_at"] <= merged["start_at"]:
        raise HTTPException(400, "End time must be after the start time.")
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE placements SET {sets} WHERE id=?", (*fields.values(), pid))
        u = db.row("SELECT title FROM uploads WHERE id=?", (p["upload_id"],))
        db.audit(me.username, f"updated {p['mode']} '{u['title'] if u else pid}': {', '.join(fields)}")
    return {"ok": True}


@app.delete("/api/placements/{pid}")
def delete_placement(pid: int, me: Principal = CurrentUser):
    p = _get("placements", pid)
    db.execute("DELETE FROM placements WHERE id=?", (pid,))
    u = db.row("SELECT title FROM uploads WHERE id=?", (p["upload_id"],))
    db.audit(me.username, f"removed {p['mode']} '{u['title'] if u else pid}'")
    return {"ok": True}


# ---- Users (local auth only) -----------------------------------------------
def _users_enabled():
    if not auth.manages_users():
        raise HTTPException(404, "User management is handled by the external auth provider.")


@app.get("/api/users")
def list_users(me: Principal = CurrentUser):
    _users_enabled()
    return db.rows("SELECT id, username, created_at FROM users ORDER BY username COLLATE NOCASE")


@app.post("/api/users")
def add_user(body: NewUser, me: Principal = CurrentUser):
    _users_enabled()
    validate_new_password(body.password)
    if db.row("SELECT id FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "That username exists.")
    db.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
               (body.username, hash_password(body.password), db.now_ms()))
    db.audit(me.username, f"added user '{body.username}'")
    return {"ok": True}


@app.put("/api/users/{uid}/password")
def set_password(uid: int, body: NewPassword, me: Principal = CurrentUser):
    _users_enabled()
    u = _get("users", uid)
    validate_new_password(body.password)
    db.execute("UPDATE users SET password_hash=?, session_gen=session_gen+1 WHERE id=?",
               (hash_password(body.password), uid))
    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.audit(me.username, f"reset password for '{u['username']}'")
    return {"ok": True, "self": u["username"].lower() == me.username.lower()}


@app.delete("/api/users/{uid}")
def delete_user(uid: int, me: Principal = CurrentUser):
    _users_enabled()
    u = _get("users", uid)
    if u["username"].lower() == me.username.lower():
        raise HTTPException(400, "You can't delete your own account.")
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] <= 1:
            raise HTTPException(400, "Keep at least one local account.")
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    db.audit(me.username, f"deleted user '{u['username']}'")
    return {"ok": True}


# ---- Activity --------------------------------------------------------------
@app.get("/api/audit")
def audit_log(limit: int = 300, me: Principal = CurrentUser):
    return db.rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),))


# ---------------------------------------------------------------------------
_TABLES = {"streams", "displays", "uploads", "placements", "users"}


def _get(table: str, rid: int) -> dict:
    assert table in _TABLES
    r = db.row(f"SELECT * FROM {table} WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, f"Not found ({table[:-1]} {rid})")
    return r
