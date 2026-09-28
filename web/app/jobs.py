"""
Web-side media handling. The web container never parses uploaded content:

  upload  -> check extension + magic bytes -> store under a random name -> write a job ticket
  worker  -> converts in its own sandboxed container (no network, no DB, no secrets)
  ingest  -> treat the worker's output as untrusted: strict names, regular files only,
             size caps, magic-byte check, then copy into rendered/ (the only served folder)
"""
import json
import os
import re
import shutil
import stat
import threading
import time
import logging
from contextlib import contextmanager
from typing import BinaryIO
import uuid
from pathlib import Path

from . import config, db

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".mpg", ".mpeg"}
DECK_EXT = {".pptx", ".ppt", ".odp", ".key", ".pdf", ".ppsx", ".pps"}
ALLOWED_EXT = IMAGE_EXT | VIDEO_EXT | DECK_EXT

OUT_NAME = re.compile(r"[0-9]{3}(_t)?\.(jpg|mp4)")
MAX_SLIDES = 500
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_VIDEO_BYTES = 8 * 1024 * 1024 * 1024


def kind_for(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in DECK_EXT:
        return "deck"
    return None


# ---------------------------------------------------------------------------
# Magic bytes: the file must actually be what its extension claims.
# ---------------------------------------------------------------------------
def _is_zip(h: bytes) -> bool:
    return h[:4] == b"PK\x03\x04"


def _is_ole(h: bytes) -> bool:
    return h[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _is_isobmff(h: bytes) -> bool:
    return h[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")


MAGIC = {
    ".jpg": lambda h: h[:3] == b"\xff\xd8\xff",
    ".jpeg": lambda h: h[:3] == b"\xff\xd8\xff",
    ".png": lambda h: h[:8] == b"\x89PNG\r\n\x1a\n",
    ".gif": lambda h: h[:6] in (b"GIF87a", b"GIF89a"),
    ".webp": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP",
    ".bmp": lambda h: h[:2] == b"BM",
    ".tif": lambda h: h[:4] in (b"II*\x00", b"MM\x00*"),
    ".tiff": lambda h: h[:4] in (b"II*\x00", b"MM\x00*"),
    ".pdf": lambda h: b"%PDF-" in h[:1024],
    ".pptx": _is_zip, ".ppsx": _is_zip, ".odp": _is_zip, ".key": _is_zip,
    ".ppt": _is_ole, ".pps": _is_ole,
    ".mp4": _is_isobmff, ".m4v": _is_isobmff, ".mov": _is_isobmff,
    ".webm": lambda h: h[:4] == b"\x1a\x45\xdf\xa3",
    ".mkv": lambda h: h[:4] == b"\x1a\x45\xdf\xa3",
    ".avi": lambda h: h[:4] == b"RIFF" and h[8:12] == b"AVI ",
    ".wmv": lambda h: h[:4] == b"\x30\x26\xb2\x75",
    ".mpg": lambda h: h[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"),
    ".mpeg": lambda h: h[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"),
}


def magic_ok(ext: str, head: bytes) -> bool:
    check = MAGIC.get(ext)
    return bool(check and check(head))


LOG = logging.getLogger(__name__)
_LOCKS = [threading.RLock() for _ in range(64)]
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def _lock(upload_id: int):
    return _LOCKS[upload_id % len(_LOCKS)]


def new_original_name(ext: str) -> str:
    return f"{uuid.uuid4().hex}{ext}"


def _job_path(upload_id: int) -> Path:
    return config.ORIGINALS_DIR / f"{upload_id}.job.json"


def submit(upload_id: int) -> None:
    with _lock(upload_id):
        up = db.row("SELECT * FROM uploads WHERE id=? AND status='processing'", (upload_id,))
        if not up:
            return
        token = uuid.uuid4().hex
        ticket = {"id": upload_id, "job_token": token, "source": up["original_file"], "kind": up["kind"],
                  "target_w": config.TARGET_WIDTH, "target_h": config.TARGET_HEIGHT, "crf": config.VIDEO_CRF}
        db.execute("UPDATE uploads SET job_token=? WHERE id=?", (token, upload_id))
        tmp = config.ORIGINALS_DIR / f".{upload_id}-{token}.tmp"
        try:
            with tmp.open("x") as fh:
                json.dump(ticket, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, _job_path(upload_id))
            _remove_dir(config.WORK_DIR / str(upload_id))
        finally:
            tmp.unlink(missing_ok=True)


def _remove_dir(path: Path) -> None:
    # shutil.rmtree is fd-based / symlink-resistant on the supported Linux host.
    if path.is_symlink():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


def delete_files(upload: dict) -> None:
    with _lock(upload["id"]):
        _job_path(upload["id"]).unlink(missing_ok=True)
        _remove_dir(config.WORK_DIR / str(upload["id"]))
        _remove_dir(config.RENDERED_DIR / str(upload["id"]))
        if re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{2,5}", upload["original_file"] or ""):
            (config.ORIGINALS_DIR / upload["original_file"]).unlink(missing_ok=True)


def worker_alive() -> bool:
    try:
        st = (config.WORK_DIR / ".heartbeat").lstat()
        return stat.S_ISREG(st.st_mode) and 0 <= time.time() - st.st_mtime < 30
    except OSError:
        return False


class Reject(Exception):
    """Worker output does not satisfy the file contract."""


@contextmanager
def _regular_fd(directory_fd: int, name: str, limit: int):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise Reject(f"{name}: not a single-link regular file")
        if not 0 < st.st_size <= limit:
            raise Reject(f"{name}: invalid size")
        yield fd, st
    finally:
        os.close(fd)


def _identity(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _read_json(directory_fd: int, name: str, limit: int) -> dict:
    with _regular_fd(directory_fd, name, limit) as (fd, before):
        pieces = []
        size = 0
        while chunk := os.read(fd, min(65536, limit + 1 - size)):
            pieces.append(chunk)
            size += len(chunk)
            if size > limit:
                raise Reject("Manifest grew during read")
        if size != before.st_size or _identity(before) != _identity(os.fstat(fd)):
            raise Reject("Manifest changed during read")
        value = json.loads(b"".join(pieces))
        if not isinstance(value, dict):
            raise Reject("Manifest must be an object")
        return value


def _validate_manifest(manifest: dict, names: set[str], token: str, graphic: bool = False) -> list[dict]:
    if manifest.get("job_token") != token:
        raise Reject("Stale conversion generation")
    slides = manifest.get("slides")
    if not isinstance(slides, list) or not 1 <= len(slides) <= MAX_SLIDES:
        raise Reject("Invalid slide list")
    if graphic and len(slides) != 1:
        raise Reject('A graphic must have exactly one rendition')
    expected = {"result.json"}
    clean = []
    for idx, slide in enumerate(slides):
        if not isinstance(slide, dict) or slide.get("kind") not in {"image", "video"}:
            raise Reject("Invalid slide entry")
        kind = slide["kind"]
        ext = Path(slide.get('file', '')).suffix
        if graphic:
            if kind != 'image' or ext not in {'.png', '.gif'}:
                raise Reject('Graphics must be rasterized PNG or GIF')
            file = f"{idx:03d}{ext}"
        else:
            file = f"{idx:03d}.{'mp4' if kind == 'video' else 'jpg'}"
        thumb = f"{idx:03d}_t.jpg"
        if slide.get("file") != file or slide.get("thumb") != thumb:
            raise Reject("Invalid or duplicate output name")
        duration = slide.get("duration_ms") if kind == "video" else None
        if kind == "video" and (type(duration) is not int or not 0 < duration <= 14_400_000):
            raise Reject("Invalid video duration")
        expected.update((file, thumb))
        clean.append({"idx": idx, "kind": kind, "file": file, "thumb": thumb, "duration_ms": duration})
    if names != expected:
        raise Reject("Missing or unreferenced output files")
    return clean


def _copy_checked(directory_fd: int, name: str, dest: Path, budget: int) -> int:
    limit = min(20 * 1048576 if name.endswith(('.png', '.gif')) else MAX_VIDEO_BYTES if name.endswith(".mp4") else MAX_IMAGE_BYTES, budget)
    with _regular_fd(directory_fd, name, limit) as (fd, before):
        total = 0
        head = b""
        tail = b""
        with dest.open("xb") as out:
            while chunk := os.read(fd, min(1048576, limit + 1 - total)):
                total += len(chunk)
                if total > limit:
                    raise Reject("Output exceeds its size budget")
                if not head:
                    head = chunk[:32]
                tail = (tail + chunk)[-12:]
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if total != before.st_size or _identity(before) != _identity(os.fstat(fd)):
            raise Reject("Output changed during copy")
        # Check the bytes actually copied, not a separately opened source pathname.
        if name.endswith(".jpg"):
            if not magic_ok(".jpg", head) or tail[-2:] != b"\xff\xd9":
                raise Reject("Invalid JPEG signature")
        elif name.endswith('.png'):
            if not magic_ok('.png', head) or head[12:16] != b'IHDR' or tail != b'\x00\x00\x00\x00IEND\xaeB`\x82':
                raise Reject('Invalid PNG signature')
            width, height = int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
            if not 0 < width <= 2048 or not 0 < height <= 2048 or width * height > 4194304:
                raise Reject('Oversized graphic')
        elif name.endswith('.gif'):
            width, height = int.from_bytes(head[6:8], 'little'), int.from_bytes(head[8:10], 'little')
            if not magic_ok('.gif', head) or tail[-1:] != b';' or not 0 < width <= 1280 or not 0 < height <= 1280:
                raise Reject('Invalid GIF signature or size')
        elif head[4:8] != b"ftyp":
            raise Reject("Invalid MP4 signature")
        return total


def _ingest(upload_id: int) -> None:
    with _lock(upload_id):
        up = db.row("SELECT * FROM uploads WHERE id=? AND status='processing'", (upload_id,))
        if not up or not up["job_token"]:
            return
        folder = config.WORK_DIR / str(upload_id)
        staging = config.RENDERED_DIR / f".in-{upload_id}-{up['job_token']}"
        try:
            directory_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        except OSError:
            _reject(upload_id, "Output directory is invalid")
            return
        try:
            names = set()
            # Bound enumeration itself, not just the size of a completed list.
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    names.add(entry.name)
                    if len(names) > MAX_SLIDES * 2 + 1:
                        raise Reject("Too many output entries")
            if "error.json" in names:
                error = _read_json(directory_fd, "error.json", 65536)
                if names != {"error.json"} or error.get("job_token") != up["job_token"]:
                    raise Reject("Invalid conversion error manifest")
                msg = error.get("error")
                if not isinstance(msg, str):
                    raise Reject("Invalid conversion error")
                db.execute("UPDATE uploads SET status='error', error=? WHERE id=? AND job_token=?",
                           (msg[:300], upload_id, up["job_token"]))
                _finish(upload_id)
                return
            manifest = _read_json(directory_fd, "result.json", 1048576)
            slides = _validate_manifest(manifest, names, up["job_token"], graphic=up["kind"] == "graphic")
            _remove_dir(staging)
            staging.mkdir(mode=0o750)
            budget = config.MAX_INGEST_MB * 1048576
            for slide in slides:
                for name in (slide["file"], slide["thumb"]):
                    budget -= _copy_checked(directory_fd, name, staging / name, budget)
            parent = config.RENDERED_DIR / str(upload_id)
            parent.mkdir(mode=0o750, exist_ok=True)
            dest = parent / up["job_token"]
            with db.tx() as c:
                c.execute("BEGIN IMMEDIATE")
                current = c.execute("SELECT job_token, status FROM uploads WHERE id=?", (upload_id,)).fetchone()
                if not current or current["job_token"] != up["job_token"] or current["status"] != "processing":
                    return
                # Every rendition gets a new immutable URL. Existing renditions are never overwritten.
                if dest.exists():
                    _remove_dir(dest)  # Recovery after a crash before the DB commit; not yet published in the DB.
                os.replace(staging, dest)
                c.execute("DELETE FROM slides WHERE upload_id=?", (upload_id,))
                for slide in slides:
                    prefix = f"{upload_id}/{up['job_token']}"
                    c.execute("INSERT INTO slides(upload_id, idx, kind, file, thumb, duration_ms) VALUES (?,?,?,?,?,?)",
                              (upload_id, slide["idx"], slide["kind"], f"{prefix}/{slide['file']}",
                               f"{prefix}/{slide['thumb']}", slide["duration_ms"]))
                c.execute("UPDATE uploads SET status='ready', error=NULL WHERE id=?", (upload_id,))
                c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)",
                          (db.now_ms(), "system", f"converted '{up['title']}' ({len(slides)} slides)"))
            _finish(upload_id)
        except (Reject, ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
            _reject(upload_id, str(exc)[:180])
        finally:
            os.close(directory_fd)
            _remove_dir(staging)


def _reject(upload_id: int, reason: str) -> None:
    LOG.warning("Rejected worker output for upload %s: %s", upload_id, reason)
    db.execute("UPDATE uploads SET status='error', error=? WHERE id=? AND status='processing'",
               ("Converted output failed validation. See Activity.", upload_id))
    db.audit("system", f"rejected output for upload {upload_id}: {reason}")
    _finish(upload_id)


def _finish(upload_id: int) -> None:
    _job_path(upload_id).unlink(missing_ok=True)
    _remove_dir(config.WORK_DIR / str(upload_id))


def store_upload(source: BinaryIO, name: str, title: str, username: str, *, transfer_key: str | None = None, graphic: bool = False) -> dict:
    from fastapi import HTTPException
    if config.REQUIRE_MEDIA_MARKERS:
        from .storage_guard import verify_media
        verify_media()
    if transfer_key:
        if not re.fullmatch(r"[0-9a-f]{32}", transfer_key):
            raise ValueError("Invalid transfer ID")
        prior = db.row("SELECT id,kind FROM uploads WHERE transfer_key=?", (transfer_key,))
        if prior:
            return prior
    name = Path(name.replace("\\", "/")).name
    ext = Path(name).suffix.lower()
    kind = 'graphic' if graphic and ext in {'.png', '.svg', '.gif'} else kind_for(name) if not graphic else None
    if not kind:
        raise HTTPException(400, "Unsupported file type.")
    source.seek(0)
    head = source.read(1024)
    # SVG has no reliable magic signature: accept bounded UTF-8 XML-looking input only
    # here; full strict XML validation and rasterization happen in the isolated worker.
    svg_candidate = graphic and ext == '.svg' and head.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<')
    if not svg_candidate and not magic_ok(ext, head):
        raise HTTPException(400, f"The file contents do not match {ext}.")
    stored = f"{transfer_key}{ext}" if transfer_key else new_original_name(ext)
    dest = config.ORIGINALS_DIR / stored
    # A single persistent transfer worker owns this name. An interrupted copy has
    # no database row yet and is safe to replace before retrying from byte zero.
    if transfer_key:
        dest.unlink(missing_ok=True)
    limit = min(config.MAX_UPLOAD_MB * 1048576, (2 if ext == '.svg' else 32) * 1048576) if graphic else config.MAX_UPLOAD_MB * 1048576
    size = len(head)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(head)
            while chunk := source.read(1048576):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "File is too large.")
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        clean_title = (title.strip() or Path(name).stem or "Untitled")[:120]
        with db.tx() as c:
            c.execute("BEGIN IMMEDIATE")
            uid = db.allocate_id(c, "uploads")
            c.execute("INSERT INTO uploads(id,title,original_name,original_file,kind,status,created_by,created_at,transfer_key) "
                      "VALUES (?,?,?,?,?,'processing',?,?,?)",
                      (uid, clean_title, name[:200], stored, kind, username, db.now_ms(), transfer_key))
            c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)",
                      (db.now_ms(), username, f"uploaded '{clean_title}'"))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    try:
        submit(uid)
    except OSError:
        # Startup recovery reissues missing tickets; don't lose the accepted original.
        LOG.exception("Could not issue ticket for upload %s; will retry", uid)
    return {"id": uid, "kind": kind}


def _sweep_orphans() -> None:
    active = {r["id"] for r in db.rows("SELECT id FROM uploads WHERE status='processing'")}
    cutoff = time.time() - 120
    for p in config.WORK_DIR.iterdir():
        if p.name.isdigit() and int(p.name) not in active and p.lstat().st_mtime < cutoff:
            _remove_dir(p)
    for p in config.ORIGINALS_DIR.glob("*.job.json"):
        n = p.name.split(".")[0]
        if n.isdigit() and int(n) not in active and p.lstat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
    # Bound session and login metadata independently of future sign-ins.
    db.execute("DELETE FROM sessions WHERE expires_at<=?", (db.now_ms(),))
    db.execute("DELETE FROM auth_attempts WHERE expires_at<=?", (db.now_ms(),))


def _loop() -> None:
    last_sweep = 0.0
    while not _STOP.is_set():
        try:
            pending = db.rows("SELECT id, job_token FROM uploads WHERE status='processing'")
            for up in pending:
                if _STOP.is_set():
                    break
                try:
                    if not up["job_token"] or not _job_path(up["id"]).exists():
                        submit(up["id"])
                    _ingest(up["id"])
                except Exception:
                    LOG.exception("Ingest failed for upload %s", up["id"])
            if time.monotonic() - last_sweep > 60:
                _sweep_orphans()
                last_sweep = time.monotonic()
        except Exception:
            LOG.exception("Ingest scan failed")
        _STOP.wait(2)


def start() -> None:
    global _THREAD
    if not config.INGEST_ENABLED or (_THREAD and _THREAD.is_alive()):
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="ingest", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()
    if _THREAD:
        _THREAD.join(timeout=5)
