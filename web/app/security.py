"""HTTP boundary controls. No request bodies are buffered by this middleware."""
import asyncio
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from . import config

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def check_write(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("x-signage") != "1":
        raise HTTPException(403, "Missing request header.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected.")
    origin = request.headers.get("origin")
    if origin:
        # Use the validated Host header, never a caller-supplied forwarded host.
        expected = f"{request.scope['scheme']}://{request.headers.get('host', '')}"
        allowed = config.ADMIN_ORIGINS or [expected]
        if origin not in allowed or origin == "null":
            raise HTTPException(403, "Request origin is not allowed.")


class BoundaryMiddleware:
    def __init__(self, app):
        self.app = app
        self.active_uploads = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path = scope["path"]
        method = scope["method"]
        upload = method == "POST" and path in {"/api/uploads", "/api/graphics"}
        limit = (32 * 1048576 + 65536 if path == "/api/graphics" else config.MAX_UPLOAD_MB * 1048576 + 65536) if upload else 65536
        started = False
        consumed = 0
        length = headers.get("content-length")
        hosts = [v for k, v in scope["headers"] if k.lower() == b"host"]
        host = hosts[0].decode("latin1") if len(hosts) == 1 else ""
        try:
            parsed = urlsplit("//" + host)
            hostname = parsed.hostname
            valid_host = bool(hostname and parsed.netloc == host and not parsed.username
                              and not parsed.password and not parsed.path and not parsed.query
                              and not parsed.fragment and not any(c in host for c in "\\\t\r\n "))
            _ = parsed.port  # Reject malformed ports, too.
        except ValueError:
            valid_host = False
            hostname = None

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                h = MutableHeaders(scope=message)
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "same-origin"
                h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                h["X-Robots-Tag"] = "noindex, nofollow"
                if path.startswith("/media/"):
                    h["Content-Security-Policy"] = "default-src 'none'; sandbox"
                    h["Cross-Origin-Resource-Policy"] = "same-origin"
                else:
                    player = path.startswith(("/display/", "/preview/", "/stream/"))
                    media = " ".join(config.SOURCE_MEDIA_ORIGINS) if player else ""
                    frames = " ".join(config.SOURCE_FRAME_ORIGINS) if player else ""
                    h["Content-Security-Policy"] = (
                        "default-src 'none'; script-src 'self'; style-src 'self'; "
                        f"img-src 'self' data: blob: {media}; media-src 'self' blob: {media}; "
                        f"connect-src 'self' {media}; worker-src 'self' blob:; "
                        f"font-src 'self'; frame-src 'self' {frames}; object-src 'none'; base-uri 'none'; "
                        "form-action 'self'; frame-ancestors " + ("'self'" if player else "'none'")
                    )
                    h["X-Frame-Options"] = "SAMEORIGIN" if player else "DENY"
                if path.startswith(("/api/", "/auth/", "/display/", "/preview/", "/stream/")) or path in ("/", "/login", "/logout", "/setup"):
                    h.setdefault("Cache-Control", "no-store")
                if (config.COOKIE_SECURE or config.COOKIE_AUTO) and scope["scheme"] == "https":
                    h["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        async def reject(status, detail, extra=None):
            await JSONResponse({"detail": detail}, status_code=status,
                               headers=extra)(scope, receive, guarded_send)

        if not valid_host or not config.host_allowed(hostname):
            if valid_host and config.HOSTS_AUTO:
                return await reject(400, f"'{hostname}' is not an allowed name for this portal. Open it by IP "
                                         "address and add the name under Settings → Access names.")
            return await reject(400, "Invalid host.")
        player_only_hosts = set(config.PLAYER_ONLY_HOSTS)
        if config.PLAYER_BASE_URL:
            base_host = urlsplit(config.PLAYER_BASE_URL).hostname
            if all(urlsplit(origin).hostname != base_host for origin in config.ADMIN_ORIGINS):
                player_only_hosts.add(base_host)
        if hostname in player_only_hosts:
            player_asset = path in {"/static/player.js", "/static/player.css", "/static/overlay.js", "/static/overlay.css", "/static/feed.svg", "/static/vendor/hls.min.js"}
            public_path = path.startswith(("/display/", "/stream/", "/preview/", "/api/player/", "/media/")) or path == "/healthz" or player_asset
            if not public_path:
                return await reject(404, "Not found.")
            if method not in {"GET", "HEAD", "OPTIONS"}:
                return await reject(405, "Playback is view-only.", {"Allow": "GET, HEAD"})
        if path.startswith(("/display/", "/stream/", "/preview/", "/api/player/")) and method not in SAFE_METHODS:
            return await reject(405, "Playback is view-only.", {"Allow": "GET, HEAD"})
        admin_route = (path in {"/", "/login", "/logout", "/setup"} or path.startswith("/auth/")
                       or (path.startswith("/api/") and not path.startswith("/api/player/")
                           and path != "/api/site"))
        if admin_route and config.COOKIE_SECURE and not config.ALLOW_INSECURE_DEV and scope["scheme"] != "https":
            return await reject(400, "Administrator access requires HTTPS.")
        lengths = [v for k, v in scope["headers"] if k.lower() == b"content-length"]
        if len(lengths) > 1 or (length and headers.get("transfer-encoding")):
            return await reject(400, "Ambiguous request framing.")
        if length is not None:
            if len(length) > 20 or not length.isascii() or not length.isdecimal():
                return await reject(400, "Invalid content length.")
            if int(length) > limit:
                return await reject(413, "Request body is too large.")
        # Players only need a single byte range. Bound parsing before StaticFiles.
        ranges = headers.get("range", "")
        if len(ranges) > 128 or "," in ranges:
            return await reject(416, "Only one byte range is supported.")
        if method not in SAFE_METHODS:
            try:
                check_write(Request(scope))
            except HTTPException as exc:
                return await reject(exc.status_code, exc.detail)
        if upload and self.active_uploads >= config.MAX_CONCURRENT_UPLOADS:
            return await reject(429, "Upload capacity is busy. Try again shortly.", {"Retry-After": "5"})

        async def bounded_receive():
            nonlocal consumed
            # This is a per-read idle timeout. Enforce a total timeout at the reverse proxy as well.
            try:
                message = await asyncio.wait_for(receive(), timeout=config.BODY_IDLE_SECONDS)
            except TimeoutError as exc:
                raise HTTPException(408, "Request body timed out.") from exc
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise HTTPException(413, "Request body is too large.")
            return message

        if upload:
            self.active_uploads += 1
        try:
            await self.app(scope, bounded_receive, guarded_send)
        except HTTPException as exc:
            if started:
                raise
            await reject(exc.status_code, exc.detail)
        finally:
            if upload:
                self.active_uploads -= 1
