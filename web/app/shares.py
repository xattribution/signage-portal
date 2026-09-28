"""Admin-only shared-folder browser and persistent import/export queue.

The operating system mounts SMB/NFS. This process never holds mount privileges,
SMB credentials, or a Docker socket. A single bounded daemon owns file transfers.
"""
import asyncio
import errno
import hashlib
import json
import logging
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from . import config, db, jobs
from .auth import CurrentUser
from .auth.base import Principal
from .schemas import ShareImport, ShareExport
from .storage_guard import directory, marker, network_filesystem

router = APIRouter()
LOG = logging.getLogger(__name__)
MAX_ENTRIES = 2000
SHARES = {}
_STOP = threading.Event()
_THREAD = None
_BROWSE_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class Share:
    id: str
    name: str
    path: Path
    volume_id: str
    read_only: bool = True
    require_network: bool = True


def load_config():
    global SHARES
    if not config.SHARES_CONFIG:
        SHARES = {}
        return
    fd = os.open(config.SHARES_CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 65536:
            raise ValueError("Invalid shares configuration file")
        entries = json.loads(os.read(fd, 65537))
    finally:
        os.close(fd)
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError("Configure at most 16 shares")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"id", "name", "path", "volume_id", "read_only", "require_network"}:
            raise ValueError("Invalid share fields")
        share = Share(**{**entry, "path": Path(entry["path"])})
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", share.id) or share.id in result
                or not isinstance(share.name, str) or not 1 <= len(share.name) <= 80
                or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", share.volume_id)
                or type(share.read_only) is not bool or type(share.require_network) is not bool
                or not share.path.is_absolute() or ".." in share.path.parts or len(share.path.parts) < 3):
            raise ValueError("Invalid share configuration")
        if not share.require_network and not config.ALLOW_INSECURE_DEV:
            raise ValueError("Production shares must be verified NFS/SMB filesystems")
        # Configuration is operator-owned, but fail loudly on dangerous folder choices.
        protected = [config.DATA_DIR, config.ORIGINALS_DIR, config.WORK_DIR, config.RENDERED_DIR,
                     Path(__file__).resolve().parent, Path("/etc"), Path("/proc"), Path("/sys"), Path("/dev")]
        for other in protected:
            other = other.absolute()
            if share.path.is_relative_to(other) or other.is_relative_to(share.path):
                raise ValueError("Shared-folder browser must not overlap private application storage")
        result[share.id] = share
    SHARES = result


def get_share(sid):
    if sid not in SHARES:
        raise HTTPException(404, "No such approved share.")
    return SHARES[sid]


def parts(path: str) -> list[str]:
    if path == "":
        return []
    bits = path.split("/")
    if (len(path) > 2048 or len(bits) > 32 or "\\" in path
            or any(not part or part.startswith(".") or ":" in part or len(part.encode()) > 255 for part in bits)
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise HTTPException(400, "Use a relative path inside the approved shared folder.")
    return bits


@contextmanager
def share_dir(share: Share, path=""):
    bits = parts(path)
    with directory(share.path) as root:
        if share.require_network:
            network_filesystem(root)
        marker(root, share.volume_id)
        device = os.fstat(root).st_dev
        fd = os.dup(root)
        try:
            for part in bits:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=fd)
                os.close(fd)
                fd = nxt
                if os.fstat(fd).st_dev != device:
                    raise OSError("Submount boundaries cannot be browsed")
            yield fd
        finally:
            os.close(fd)


@contextmanager
def regular(parent: int, name: str, limit: int):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_dev != os.fstat(parent).st_dev
                or not 0 < st.st_size <= limit):
            raise HTTPException(400, "Select a supported regular file within the size limit; links are not accepted.")
        yield fd, st
    finally:
        os.close(fd)


def _identity(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


class StableReader:
    """Detect concurrent NAS edits before jobs.store_upload commits the library item."""
    def __init__(self, file, before):
        self.file, self.before = file, before

    def seek(self, offset):
        return self.file.seek(offset)

    def read(self, count):
        chunk = self.file.read(count)
        if not chunk and _identity(os.fstat(self.file.fileno())) != _identity(self.before):
            raise HTTPException(409, "The source changed during import. Wait for it to finish saving and retry.")
        return chunk


def browse(share, path, offset, limit):
    output = []
    with share_dir(share, path) as folder:
        with os.scandir(folder) as iterator:
            for count, entry in enumerate(iterator, 1):
                if count > MAX_ENTRIES:
                    raise HTTPException(413, "This directory is too large to browse safely; split it into smaller folders.")
                if entry.name.startswith("."):
                    continue
                try:
                    parts(entry.name)
                    st = entry.stat(follow_symlinks=False)
                    is_dir = stat.S_ISDIR(st.st_mode)
                    if st.st_dev != os.fstat(folder).st_dev or (not is_dir and
                            (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or not (jobs.kind_for(entry.name) or Path(entry.name).suffix.lower() == '.svg'))):
                        continue
                    output.append({"name": entry.name, "directory": is_dir,
                                   "size": None if is_dir else st.st_size,
                                   "modified_ms": int(st.st_mtime * 1000)})
                except (OSError, HTTPException):
                    continue
    output.sort(key=lambda e: (not e["directory"], e["name"].casefold(), e["name"]))
    return {"share_id": share.id, "path": path, "read_only": share.read_only,
            "entries": output[offset:offset + limit],
            "next_offset": offset + limit if offset + limit < len(output) else None}


async def bounded_browse(fn, *args):
    # Kernel NFS I/O cannot always be cancelled. Limit outstanding calls rather
    # than spawn an unbounded number of threads each time an HTTP request times out.
    if not _BROWSE_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "Shared storage is busy or unavailable. Try again shortly.")
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(value, error):
        if not future.done():
            future.set_exception(error) if error else future.set_result(value)

    def work():
        value, error = None, None
        try:
            value = fn(*args)
        except HTTPException as exc:
            error = exc
        except (OSError, ValueError, UnicodeError):
            error = HTTPException(503, "Share unavailable, identity mismatch, or access denied. Check the host mount.")
        except Exception:
            LOG.exception("Shared-folder browse failed")
            error = HTTPException(503, "Shared-folder operation failed.")
        finally:
            _BROWSE_SLOTS.release()
        try:
            loop.call_soon_threadsafe(deliver, value, error)
        except RuntimeError:
            pass  # Request loop closed while the NAS was unavailable.

    threading.Thread(target=work, name="share-browse", daemon=True).start()
    try:
        return await asyncio.wait_for(future, timeout=8)
    except TimeoutError:
        raise HTTPException(504, "Shared storage did not respond. No local substitute was created.") from None


@router.get("/api/shares")
def list_shares(me: Principal = CurrentUser):
    # Listing configured names never touches the NAS and cannot hang on a dead mount.
    return [{"id": s.id, "name": s.name, "read_only": s.read_only} for s in SHARES.values()]


@router.get("/api/shares/{sid}/browse")
async def browse_share(sid: str, path: str = "", offset: int = Query(0, ge=0, le=MAX_ENTRIES),
                       limit: int = Query(100, ge=1, le=200), me: Principal = CurrentUser):
    share = get_share(sid)
    parts(path)
    return await bounded_browse(browse, share, path, offset, limit)


def queue(direction, share, path, me, uid=None, title="", as_graphic=False):
    parts(path)
    if direction == "export" and share.read_only:
        raise HTTPException(403, "This shared folder is read-only.")
    jid = uuid.uuid4().hex
    output_name = None
    if uid:
        up = db.row("SELECT * FROM uploads WHERE id=?", (uid,))
        if not up or up["kind"] == "feed":
            raise HTTPException(400, "Only uploaded files have an original to export.")
        suffix = Path(up["original_file"]).suffix.lower()
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(up["original_name"]).stem).strip("-")[:70] or "content"
        output_name = f"{stem}--{jid[:12]}{suffix}"
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT COUNT(*) FROM library_transfers WHERE status IN ('queued','running')").fetchone()[0] >= 32:
            raise HTTPException(429, "The transfer queue is full. Let existing jobs finish.")
        c.execute("INSERT INTO library_transfers(id,direction,share_id,relative_path,upload_id,output_name,title,created_by,created_at,updated_at,as_graphic) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (jid,direction,share.id,path,uid,output_name,title,me.username,db.now_ms(),db.now_ms(),int(as_graphic)))
        c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)",
                  (db.now_ms(), me.username, f"queued {direction} {jid[:12]} on shared folder '{share.name}'"))
    return {"id": jid, "status": "queued"}


@router.post("/api/shares/{sid}/import", status_code=202)
def import_share(sid: str, body: ShareImport, me: Principal = CurrentUser):
    supported = Path(body.path).suffix.lower() in {'.png', '.svg', '.gif'} if body.as_graphic else bool(jobs.kind_for(body.path))
    if not supported:
        raise HTTPException(400, "Select a supported file; use As graphic for PNG, SVG or GIF overlays.")
    return queue("import", get_share(sid), body.path, me, title=body.title, as_graphic=body.as_graphic)


@router.post("/api/shares/{sid}/export", status_code=202)
def export_share(sid: str, body: ShareExport, me: Principal = CurrentUser):
    return queue("export", get_share(sid), body.path, me, uid=body.upload_id)


def public_transfer(row):
    return {**{k: row[k] for k in ("id","direction","share_id","relative_path","upload_id","output_name",
                                   "status","error","created_at","updated_at")},
            "result": json.loads(row["result_json"]) if row["result_json"] else None}


@router.get("/api/transfers")
def list_transfers(me: Principal = CurrentUser):
    return [public_transfer(row) for row in db.rows("SELECT * FROM library_transfers ORDER BY created_at DESC LIMIT 100")]


@router.post("/api/transfers/{jid}/retry")
def retry_transfer(jid: str, me: Principal = CurrentUser):
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT id FROM library_transfers WHERE id=? AND status='error'", (jid,)).fetchone():
            raise HTTPException(409, "Only failed transfers can be retried.")
        if c.execute("SELECT COUNT(*) FROM library_transfers WHERE status IN ('queued','running')").fetchone()[0] >= 32:
            raise HTTPException(429, "The transfer queue is full. Let existing jobs finish.")
        c.execute("UPDATE library_transfers SET status='queued',error=NULL,updated_at=? WHERE id=?", (db.now_ms(), jid))
        c.execute("INSERT INTO audit(at,who,what) VALUES (?,?,?)", (db.now_ms(),me.username,f"retried transfer {jid[:12]}"))
    return {"ok": True}


def do_import(job, share):
    prior = db.row("SELECT id,kind FROM uploads WHERE transfer_key=?", (job["id"],))
    if prior:
        return prior
    bits = parts(job["relative_path"])
    with share_dir(share, "/".join(bits[:-1])) as parent:
        with regular(parent, bits[-1], config.MAX_UPLOAD_MB * 1048576) as (fd, st):
            with os.fdopen(os.dup(fd), "rb") as fh:
                return jobs.store_upload(StableReader(fh, st), bits[-1], job["title"], job["created_by"], transfer_key=job["id"], graphic=bool(job.get("as_graphic", 0)))


def digest_file(fd):
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    size = 0
    while chunk := os.read(fd, 1048576):
        digest.update(chunk)
        size += len(chunk)
        if size > config.MAX_UPLOAD_MB * 1048576:
            raise HTTPException(413, "File is too large.")
    return digest.hexdigest(), size


def do_export(job, share):
    if config.REQUIRE_MEDIA_MARKERS:
        from .storage_guard import verify_media
        verify_media()
    if share.read_only:
        raise HTTPException(403, "This shared folder is read-only.")
    up = db.row("SELECT * FROM uploads WHERE id=?", (job["upload_id"],))
    if not up or not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{2,5}", up["original_file"]):
        raise HTTPException(404, "The original upload is no longer available.")
    with share_dir(share, job["relative_path"]) as dest:
        temp = f".signage-export-{job['id']}.part"
        final = job["output_name"]
        prepared = json.loads(job["result_json"]) if job["result_json"] else None
        # Recovery after publication but before the queue's completed transaction.
        if prepared:
            try:
                fd = os.open(final, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dest)
            except FileNotFoundError:
                pass
            else:
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 2:
                        raise HTTPException(409, "Export destination is not a safe regular file.")
                    sha, size = digest_file(fd)
                    if sha != prepared["sha256"] or size != prepared["size"]:
                        raise HTTPException(409, "An existing destination differs from this export. It was not overwritten.")
                    try:
                        os.unlink(temp, dir_fd=dest)
                    except FileNotFoundError:
                        pass
                    return prepared
                finally:
                    os.close(fd)
        try:
            os.unlink(temp, dir_fd=dest)
        except FileNotFoundError:
            pass
        fdout = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=dest)
        try:
            with os.fdopen(fdout, "wb") as out, directory(config.ORIGINALS_DIR) as src:
                with regular(src, up["original_file"], config.MAX_UPLOAD_MB * 1048576) as (fd, before):
                    digest, size = hashlib.sha256(), 0
                    while chunk := os.read(fd, 1048576):
                        size += len(chunk)
                        if size > config.MAX_UPLOAD_MB * 1048576:
                            raise HTTPException(413, "File is too large.")
                        out.write(chunk)
                        digest.update(chunk)
                    if _identity(before) != _identity(os.fstat(fd)):
                        raise HTTPException(409, "The original changed during export. Retry after it finishes saving.")
                    out.flush()
                    os.fsync(out.fileno())
            result = {"name": final, "size": size, "sha256": digest.hexdigest()}
            # Persist expected bytes BEFORE making the output visible.
            db.execute("UPDATE library_transfers SET result_json=? WHERE id=?", (json.dumps(result),job["id"]))
            try:
                from .publication import publish
                publish(dest, temp, final)
            except FileExistsError:
                raise HTTPException(409, "Destination exists. Nothing was overwritten.") from None
            except OSError as exc:
                if exc.errno in {errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV, errno.EPERM}:
                    raise HTTPException(409, "The NAS must support atomic no-overwrite rename or hard-link publication for safe exports; this filesystem does not.") from None
                raise
            try:
                os.fsync(dest)
            except OSError as exc:
                if exc.errno not in {errno.EINVAL, errno.EOPNOTSUPP, errno.EBADF}:
                    raise
            return result
        finally:
            try:
                os.unlink(temp, dir_fd=dest)
            except FileNotFoundError:
                pass


def process_one():
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM library_transfers WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if row is None:
            return False
        job = dict(row)
        c.execute("UPDATE library_transfers SET status='running',updated_at=? WHERE id=?", (db.now_ms(),job["id"]))
    try:
        share = get_share(job["share_id"])
        result = do_import(job, share) if job["direction"] == "import" else do_export(job, share)
        db.execute("UPDATE library_transfers SET status='completed',result_json=?,error=NULL,updated_at=? WHERE id=?",
                   (json.dumps(result),db.now_ms(),job["id"]))
        db.audit(job["created_by"], f"completed {job['direction']} {job['id'][:12]}")
    except (HTTPException, OSError, ValueError) as exc:
        error = str(exc.detail) if isinstance(exc, HTTPException) else "Share unavailable, access denied, or storage identity changed. Check the host mount and retry."
        db.execute("UPDATE library_transfers SET status='error',error=?,updated_at=? WHERE id=?", (error,db.now_ms(),job["id"]))
        db.audit(job["created_by"], f"failed {job['direction']} {job['id'][:12]}")
    return True


def recover():
    # Single web instance/worker. A running transfer cannot be completed by an old
    # process after startup; idempotent publication handles ambiguous power loss.
    db.execute("UPDATE library_transfers SET status='queued',updated_at=? WHERE status='running'", (db.now_ms(),))


def _loop():
    while not _STOP.is_set():
        try:
            if process_one():
                continue
        except Exception:
            LOG.exception("Shared-folder transfer queue failed")
        _STOP.wait(2)


def start():
    global _THREAD
    if not config.TRANSFERS_ENABLED or (_THREAD and _THREAD.is_alive()):
        return
    recover()
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="library-transfers", daemon=True)
    _THREAD.start()


def stop():
    _STOP.set()
    if _THREAD:
        _THREAD.join(timeout=3)
