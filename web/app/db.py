"""SQLite storage. One short-lived connection per call keeps this thread-safe with the worker pool."""
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    session_gen   INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS streams (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'normal',       -- normal | screensaver (exactly one)
    fallback   TEXT NOT NULL DEFAULT 'screensaver',  -- when nothing is scheduled: screensaver | blank
    color      TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS displays (
    id         INTEGER PRIMARY KEY,
    slug       TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    stream_id  INTEGER REFERENCES streams(id) ON DELETE SET NULL,
    last_seen  INTEGER,
    last_ip    TEXT,
    now_slide  INTEGER,
    created_at INTEGER NOT NULL
);

-- One upload = one thing someone sent in (an image, a video, or a whole deck).
CREATE TABLE IF NOT EXISTS uploads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    original_name TEXT NOT NULL,
    original_file TEXT NOT NULL,
    kind          TEXT NOT NULL,            -- image | video | deck
    status        TEXT NOT NULL,            -- processing | ready | error
    error         TEXT,
    created_by    TEXT,
    created_at    INTEGER NOT NULL
);

-- Rendered, display-ready pieces of an upload (a deck has many, an image/video has one).
CREATE TABLE IF NOT EXISTS slides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id   INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    kind        TEXT NOT NULL,              -- image | video
    file        TEXT NOT NULL,              -- path relative to RENDERED_DIR
    thumb       TEXT NOT NULL,
    duration_ms INTEGER                     -- videos only
);

-- An upload placed on a stream, either in the rotation or as an override.
-- stream_id NULL = override on every stream.
CREATE TABLE IF NOT EXISTS placements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id     INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    stream_id     INTEGER REFERENCES streams(id) ON DELETE CASCADE,
    mode          TEXT NOT NULL,            -- rotation | override
    position      INTEGER NOT NULL DEFAULT 0,
    slide_seconds INTEGER NOT NULL,
    start_at      INTEGER,                  -- epoch ms, NULL = immediately
    end_at        INTEGER,                  -- epoch ms, NULL = forever (rotation only)
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id    INTEGER PRIMARY KEY,
    at    INTEGER NOT NULL,
    who   TEXT,
    what  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS auth_attempts (
    bucket TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempt_expiry ON auth_attempts(expires_at);
CREATE INDEX IF NOT EXISTS ix_upload_status ON uploads(status);
CREATE INDEX IF NOT EXISTS ix_place_upload ON placements(upload_id);
CREATE INDEX IF NOT EXISTS ix_display_stream ON displays(stream_id);
CREATE INDEX IF NOT EXISTS ix_slides_upload ON slides(upload_id, idx);
CREATE INDEX IF NOT EXISTS ix_place_stream ON placements(stream_id, mode, position);
CREATE TABLE IF NOT EXISTS retired_display_slugs (slug TEXT PRIMARY KEY);
-- Portal-editable settings (Settings page). Environment variables, when set, take precedence.
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS library_transfers (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    share_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    upload_id INTEGER,
    output_name TEXT,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    result_json TEXT,
    error TEXT,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transfer_status ON library_transfers(status,created_at);
CREATE TABLE IF NOT EXISTS presentation_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE,
    settings_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

"""


def now_ms() -> int:
    return int(time.time() * 1000)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(sql: str, params=()) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def row(sql: str, params=()) -> dict | None:
    with tx() as c:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql: str, params=()) -> int:
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def audit(who: str | None, what: str) -> None:
    execute("INSERT INTO audit(at, who, what) VALUES (?,?,?)", (now_ms(), who, what))


def init() -> None:
    config.validate()
    config.ensure_dirs()
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # Upgrade databases created before screensaver support.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(streams)")}
        if "kind" not in cols:
            conn.execute("ALTER TABLE streams ADD COLUMN kind TEXT NOT NULL DEFAULT 'normal'")
        if "fallback" not in cols:
            conn.execute("ALTER TABLE streams ADD COLUMN fallback TEXT NOT NULL DEFAULT 'screensaver'")
        conn.commit()
        conn.execute("INSERT INTO id_sequences(name,value) SELECT 'uploads',COALESCE(MAX(id),0) FROM uploads WHERE true "
                     "ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)")
        upload_cols = {r[1] for r in conn.execute("PRAGMA table_info(uploads)")}
        if "job_token" not in upload_cols:
            conn.execute("ALTER TABLE uploads ADD COLUMN job_token TEXT")
        conn.commit()
        transfer_cols = {r[1] for r in conn.execute("PRAGMA table_info(library_transfers)")}
        if 'as_graphic' not in transfer_cols:
            conn.execute("ALTER TABLE library_transfers ADD COLUMN as_graphic INTEGER NOT NULL DEFAULT 0")
        stream_cols = {r[1] for r in conn.execute("PRAGMA table_info(streams)")}
        if "presentation_json" not in stream_cols:
            conn.execute("ALTER TABLE streams ADD COLUMN presentation_json TEXT NOT NULL DEFAULT '{}'")
        if "presentation_revision" not in stream_cols:
            conn.execute("ALTER TABLE streams ADD COLUMN presentation_revision INTEGER NOT NULL DEFAULT 0")
        if "playback_key" not in stream_cols:
            conn.execute("ALTER TABLE streams ADD COLUMN playback_key TEXT")
        if "source_url" not in upload_cols:
            conn.execute("ALTER TABLE uploads ADD COLUMN source_url TEXT")
        if "feed_kind" not in upload_cols:
            conn.execute("ALTER TABLE uploads ADD COLUMN feed_kind TEXT")
        if "transfer_key" not in upload_cols:
            conn.execute("ALTER TABLE uploads ADD COLUMN transfer_key TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_transfer_key ON uploads(transfer_key)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_stream_key ON streams(playback_key)")
        conn.commit()
        # First run: three independent streams, three displays.
        if conn.execute("SELECT COUNT(*) FROM streams").fetchone()[0] == 0:
            t = now_ms()
            for name in ("Stream A", "Stream B", "Stream C"):
                conn.execute("INSERT INTO streams(name, created_at) VALUES (?,?)", (name, t))
            ids = [r[0] for r in conn.execute("SELECT id FROM streams ORDER BY id")]
            for i, sid in enumerate(ids, start=1):
                conn.execute(
                    "INSERT INTO displays(slug, name, stream_id, created_at) VALUES (?,?,?,?)",
                    (f"lobby-{i}", f"Lobby {i}", sid, t),
                )
            conn.commit()
        if not conn.execute("SELECT 1 FROM streams WHERE kind='screensaver'").fetchone():
            conn.execute("INSERT INTO streams(name, kind, fallback, created_at) VALUES ('Screensaver','screensaver','blank',?)",
                         (now_ms(),))
            conn.commit()
        for table in ("uploads", "streams", "displays"):
            # Capture legacy high-water marks before a highest-ID row can be deleted.
            conn.execute(f"INSERT INTO id_sequences(name,value) SELECT ?,COALESCE(MAX(id),0) FROM {table} WHERE true "
                         "ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)", (table,))
        for stream in conn.execute("SELECT id FROM streams WHERE playback_key IS NULL").fetchall():
            conn.execute("UPDATE streams SET playback_key=? WHERE id=?", (uuid.uuid4().hex, stream[0]))
        conn.commit()
    finally:
        conn.close()
    from . import settings
    settings.load()


def allocate_id(conn: sqlite3.Connection, table: str) -> int:
    """Never reuse public IDs after deletion, including upgraded databases."""
    if table not in {"uploads", "streams", "displays"}:
        raise ValueError("Unsupported sequence")
    maximum = conn.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]
    conn.execute("INSERT INTO id_sequences(name,value) VALUES (?,?) "
                 "ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)", (table, maximum))
    conn.execute("UPDATE id_sequences SET value=value+1 WHERE name=?", (table,))
    return conn.execute("SELECT value FROM id_sequences WHERE name=?", (table,)).fetchone()[0]
