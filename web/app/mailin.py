"""
Email-in: approved senders email attachments to a mailbox (for example a Gmail account),
and the portal imports them into the library exactly like a browser upload.

Why this exists: corporate web proxies often block uploads, but email still gets through.

How a message is handled:
  1. Only messages from an approved sender are considered. A sender is approved when a
     local account has that email with Email-in turned on, or it is on the Settings list.
  2. The From header alone is easy to forge, so by default the sender must be verified by
     the receiving mail server: the topmost Authentication-Results header, written by the
     trusted server (Gmail: mx.google.com), must show dmarc=pass, or dkim=pass for the
     sender's own domain.
  3. Each attachment goes through jobs.store_upload: extension allowlist, magic-byte check,
     size limit, then conversion in the isolated worker. Inline signature images are skipped.
  4. Imported items land in the shared library, owned by the sender's account (their "Mine"
     view). Nothing is placed on a stream automatically.
Messages are marked read after processing and every outcome is kept in email_log.
"""
import base64
import email
import email.policy
import email.utils
import hashlib
import imaplib
import io
import json
import re
import socket
import ssl
import threading
import time
import traceback
from email.message import EmailMessage
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from . import config, db, jobs

SETTINGS_KEY = "mail_in"
DEFAULTS = {
    "enabled": False,
    "host": "imap.gmail.com",
    "port": 993,
    "username": "",
    "folder": "INBOX",
    "poll_minutes": 2,
    "require_verified": True,
    "trusted_server": "mx.google.com",
    "senders": [],          # approved senders without a portal account
}
MAX_PER_ROUND = 20
MIN_INLINE_BYTES = 15 * 1024    # smaller inline images are signature logos, not content
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_HOST = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")

_poll_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_status = {"last_check_at": None, "last_ok_at": None, "last_error": None, "imported": 0}


def valid_email(value: str) -> bool:
    return bool(_EMAIL.fullmatch(value or ""))


# ---------------------------------------------------------------------------
# Configuration (stored in the settings table; the app password is encrypted)
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    key = hashlib.sha256((config.secret_key() + ":mail-in").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def load() -> dict:
    row = db.row("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY,))
    stored = json.loads(row["value"]) if row else {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS or k == "password"}}


def _password(cfg: dict) -> str:
    token = cfg.get("password") or ""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""  # The secret key changed; the password must be entered again.


def public() -> dict:
    cfg = load()
    out = {k: cfg[k] for k in DEFAULTS}
    out["password_set"] = bool(_password(cfg))
    out["status"] = dict(_status)
    out["recent"] = db.rows("SELECT at, sender, subject, status, detail FROM email_log ORDER BY id DESC LIMIT 12")
    return out


def summary() -> dict:
    """What the library needs: whether email-in is on and where to send files."""
    cfg = load()
    return {"enabled": bool(cfg["enabled"]), "address": cfg["username"] if cfg["enabled"] else ""}


def save(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(400, "Nothing to save.")
    cfg = load()
    for key, value in changes.items():
        if key == "password":
            if not isinstance(value, str) or len(value) > 256:
                raise HTTPException(400, "App password is too long.")
            value = value.replace(" ", "")  # Gmail shows app passwords in groups of four.
            cfg["password"] = _fernet().encrypt(value.encode()).decode() if value else ""
            continue
        if key not in DEFAULTS:
            raise HTTPException(400, f"Unknown email-in setting '{str(key)[:40]}'.")
        cfg[key] = _clean(key, value)
    if cfg["enabled"] and (not cfg["username"] or not _password(cfg)):
        raise HTTPException(400, "Enter the mailbox address and app password before turning email-in on.")
    with db.tx() as c:
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (SETTINGS_KEY, json.dumps(cfg)))
    _wake.set()
    return public()


def _clean(key: str, value):
    if key in ("enabled", "require_verified"):
        if not isinstance(value, bool):
            raise HTTPException(400, "Use true or false.")
        return value
    if key == "username":
        value = str(value).strip()
        if value and not valid_email(value):
            raise HTTPException(400, "Enter the mailbox as a full email address.")
        return value
    if key in ("host", "trusted_server"):
        value = str(value).strip().lower()
        if not _HOST.fullmatch(value):
            raise HTTPException(400, "Enter a server name such as imap.gmail.com, without a port or https://.")
        return value
    if key == "port":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise HTTPException(400, "Port must be a number between 1 and 65535.")
        return value
    if key == "folder":
        value = str(value).strip()
        if not value or len(value) > 100 or any(c in value for c in '\r\n"\\'):
            raise HTTPException(400, "Enter a mailbox folder name such as INBOX.")
        return value
    if key == "poll_minutes":
        if value not in (1, 2, 5, 15):
            raise HTTPException(400, "Check every 1, 2, 5 or 15 minutes.")
        return value
    if key == "senders":
        if not isinstance(value, list) or len(value) > 200:
            raise HTTPException(400, "List up to 200 approved senders.")
        out = []
        for raw in value:
            addr = str(raw).strip().lower()
            if not addr:
                continue
            if not valid_email(addr):
                raise HTTPException(400, f"'{addr[:60]}' is not a valid email address.")
            if addr not in out:
                out.append(addr)
        return out
    raise HTTPException(400, "Unknown setting.")


# ---------------------------------------------------------------------------
# Sender checks
# ---------------------------------------------------------------------------
def owner_for(address: str, cfg: dict) -> str | None:
    """Portal identity that owns mail from this address, or None if not approved."""
    address = address.lower()
    user = db.row("SELECT username FROM users WHERE email=? COLLATE NOCASE AND email_in=1", (address,))
    if user:
        return user["username"]
    return address if address in cfg["senders"] else None


def _domain_aligned(signing: str, from_domain: str) -> bool:
    signing = signing.lower().strip(".")
    return from_domain == signing or from_domain.endswith("." + signing) or signing.endswith("." + from_domain)


def _strip_comments(value: str) -> str:
    out, depth = [], 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif not depth:
            out.append(ch)
    return "".join(out)


def sender_verified(msg: EmailMessage, from_addr: str, trusted_server: str) -> tuple[bool, str]:
    """Trust only the topmost Authentication-Results header, which the receiving server writes.

    Comments are removed first: they can carry sender-controlled text such as the envelope address.
    """
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False, "no Authentication-Results header from the mail server"
    parts = [p.strip() for p in _strip_comments(str(headers[0])).lower().split(";")]
    server = parts[0].split()[0] if parts[0] else ""
    if server != trusted_server.lower():
        return False, f"authentication results came from '{server[:60]}', not {trusted_server}"
    from_domain = from_addr.rsplit("@", 1)[-1].lower()
    for res in parts[1:]:
        words = res.split()
        if not words:
            continue
        props = dict(w.split("=", 1) for w in words[1:] if "=" in w)
        if words[0] == "dmarc=pass" and props.get("header.from", "").strip(".") == from_domain:
            return True, "dmarc=pass"
        if words[0] == "dkim=pass":
            signer = props.get("header.d") or props.get("header.i", "").rsplit("@", 1)[-1]
            if signer and _domain_aligned(signer, from_domain):
                return True, "dkim=pass"
    return False, "the sender could not be verified (no dmarc=pass or matching dkim=pass)"


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------
def _attachments(msg: EmailMessage):
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if not name or disposition not in ("attachment", "inline"):
            continue
        payload = part.get_payload(decode=True) or b""
        # Signature logos arrive as small inline images; real content is attached.
        if disposition == "inline" and part.get_content_maintype() == "image" and len(payload) < MIN_INLINE_BYTES:
            continue
        yield Path(str(name).replace("\\", "/")).name[:200], payload


def _log(message_id: str, sender: str, subject: str, status: str, detail: str) -> None:
    db.execute("INSERT OR IGNORE INTO email_log(at,message_id,sender,subject,status,detail) VALUES (?,?,?,?,?,?)",
               (db.now_ms(), message_id[:300], sender[:200], subject[:200], status, detail[:500]))


def process_message(raw: bytes, cfg: dict) -> dict:
    """Import the attachments of one raw RFC 822 message. Returns a summary."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = str(msg.get("Message-ID") or "").strip() or "sha256:" + hashlib.sha256(raw).hexdigest()
    subject = re.sub(r"\s+", " ", str(msg.get("Subject") or "")).strip()[:120]
    if db.row("SELECT 1 FROM email_log WHERE message_id=?", (message_id[:300],)):
        return {"status": "duplicate", "imported": 0}
    senders = email.utils.getaddresses([str(h) for h in (msg.get_all("From") or [])])
    if len(senders) != 1 or not valid_email(senders[0][1]):
        _log(message_id, "", subject, "rejected", "The message must have exactly one sender address.")
        return {"status": "rejected", "imported": 0}
    sender = senders[0][1].lower()
    owner = owner_for(sender, cfg)
    if not owner:
        _log(message_id, sender, subject, "rejected", "This sender is not approved for email-in.")
        db.audit("email-in", f"ignored email from unapproved sender {sender[:120]}")
        return {"status": "rejected", "imported": 0}
    if cfg["require_verified"]:
        ok, why = sender_verified(msg, sender, cfg["trusted_server"])
        if not ok:
            _log(message_id, sender, subject, "rejected", "Not imported: " + why + ".")
            db.audit("email-in", f"rejected email from {sender[:120]}: {why}")
            return {"status": "rejected", "imported": 0}

    imported, problems = [], []
    attachments = list(_attachments(msg))
    for name, payload in attachments:
        if not jobs.kind_for(name):
            problems.append(f"{name}: unsupported file type")
            continue
        if len(payload) > config.MAX_UPLOAD_MB * 1048576:
            problems.append(f"{name}: larger than {config.MAX_UPLOAD_MB} MB")
            continue
        title = subject if subject and len(attachments) == 1 else Path(name).stem
        try:
            up = jobs.store_upload(io.BytesIO(payload), name, title, owner)
        except HTTPException as exc:
            problems.append(f"{name}: {exc.detail}")
            continue
        db.execute("UPDATE uploads SET via='email', sender=? WHERE id=?", (sender, up["id"]))
        imported.append(name)
    if imported:
        detail = "Imported " + ", ".join(imported) + ("." if not problems else ". Skipped " + "; ".join(problems) + ".")
        _log(message_id, sender, subject, "imported", detail)
        db.audit(owner, f"emailed in {len(imported)} file(s) from {sender[:120]}")
    else:
        detail = "No supported attachments." if not problems else "Skipped " + "; ".join(problems) + "."
        _log(message_id, sender, subject, "rejected", detail)
    _status["imported"] += len(imported)
    return {"status": "imported" if imported else "rejected", "imported": len(imported)}


# ---------------------------------------------------------------------------
# Mailbox polling
# ---------------------------------------------------------------------------
def _connect(cfg: dict):
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], ssl_context=context, timeout=30)
    conn.login(cfg["username"], _password(cfg))
    return conn


def poll_once(wait: float = 0) -> dict:
    """Check the mailbox once. "Check now" waits for a background check already in progress."""
    if not _poll_lock.acquire(timeout=wait) if wait else not _poll_lock.acquire(blocking=False):
        return {"ok": False, "error": "A check is already running. Try again in a minute."}
    cfg = load()
    if not cfg["enabled"]:
        _poll_lock.release()
        return {"ok": False, "error": "Email-in is turned off."}
    _status["last_check_at"] = db.now_ms()
    summary = {"ok": True, "checked": 0, "imported": 0}
    conn = None
    try:
        conn = _connect(cfg)
        typ, _ = conn.select(f'"{cfg["folder"]}"')
        if typ != "OK":
            raise RuntimeError(f"The folder '{cfg['folder']}' does not exist in the mailbox.")
        typ, data = conn.uid("SEARCH", None, "UNSEEN")
        uids = (data[0] or b"").split()[:MAX_PER_ROUND] if typ == "OK" else []
        limit = int(config.MAX_UPLOAD_MB * 1048576 * 1.4) + 1048576  # base64 overhead
        for uid in uids:
            typ, meta = conn.uid("FETCH", uid, "(RFC822.SIZE)")
            size_match = re.search(rb"RFC822\.SIZE (\d+)", meta[0] if meta and isinstance(meta[0], bytes) else b"")
            if size_match and int(size_match.group(1)) > limit:
                _log(f"uid:{cfg['username']}:{uid.decode()}", "", "", "rejected",
                     f"The message is larger than the {config.MAX_UPLOAD_MB} MB upload limit.")
            else:
                typ, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                if raw:
                    try:
                        result = process_message(raw, cfg)
                    except Exception as exc:  # noqa: BLE001 - one bad message must not block the rest
                        traceback.print_exc()
                        _log(f"uid:{cfg['username']}:{uid.decode()}", "", "", "rejected",
                             f"The message could not be read ({exc.__class__.__name__}).")
                        result = {"imported": 0}
                    summary["imported"] += result["imported"]
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            summary["checked"] += 1
        _status["last_ok_at"] = db.now_ms()
        _status["last_error"] = None
    except (imaplib.IMAP4.error, OSError, socket.timeout, RuntimeError, ssl.SSLError) as exc:
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, imaplib.IMAP4.error) and "authenticat" in message.lower():
            message = "The mailbox rejected the sign-in. Check the address and app password."
        _status["last_error"] = message[:300]
        summary = {"ok": False, "error": _status["last_error"]}
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - the connection may already be gone
                pass
        _poll_lock.release()
    return summary


def _loop() -> None:
    while not _stop.is_set():
        try:
            cfg = load()
            if cfg["enabled"] and cfg["username"]:
                poll_once()
            wait = cfg["poll_minutes"] * 60
        except Exception:  # noqa: BLE001 - keep the poller alive; the error is shown in Settings
            traceback.print_exc()
            wait = 120
        _wake.wait(wait)
        _wake.clear()


def start() -> None:
    _stop.clear()
    threading.Thread(target=_loop, name="mail-in", daemon=True).start()


def stop() -> None:
    _stop.set()
    _wake.set()
