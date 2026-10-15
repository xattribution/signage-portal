"""Email-in: approved, verified senders' attachments become library items; nothing else does."""
import imaplib
from email.message import EmailMessage

import pytest

from app import db, mailin

H = {"X-Signage": "1"}
GMAIL_OK = "mx.google.com; dkim=pass header.i=@corp.example header.s=s1; spf=pass smtp.mailfrom=a@corp.example; dmarc=pass (p=REJECT) header.from=corp.example"


def message(jpeg, sender="ana@corp.example", auth=GMAIL_OK, msgid="<m1@corp.example>", attach=True, inline_logo=False,
            name="poster.jpg", subject="Lobby poster"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, "inbox@gmail.com", subject, msgid
    if auth:
        m["Authentication-Results"] = auth
    m.set_content("See attached.")
    if inline_logo:
        m.add_related(b"\x89PNG\r\n\x1a\n" + b"0" * 200, maintype="image", subtype="png", filename="logo.png", disposition="inline")
    if attach:
        m.add_attachment(jpeg, maintype="image", subtype="jpeg", filename=name)
    return m.as_bytes()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(mailin, "_status", {"last_check_at": None, "last_ok_at": None, "last_error": None, "imported": 0})
    db.execute("UPDATE users SET email='ana@corp.example', email_in=1 WHERE username='admin'")
    return {**mailin.DEFAULTS, "enabled": True, "username": "inbox@gmail.com", "senders": ["kiosk@partner.example"]}


def uploads():
    return db.rows("SELECT title, created_by, via, sender, status FROM uploads")


def test_verified_account_sender_is_imported_to_their_library(cfg, jpeg):
    assert mailin.process_message(message(jpeg), cfg) == {"status": "imported", "imported": 1}
    up = uploads()
    assert up == [{"title": "Lobby poster", "created_by": "admin", "via": "email", "sender": "ana@corp.example", "status": "processing"}]
    assert not db.rows("SELECT * FROM placements")  # never auto-placed
    assert db.row("SELECT status FROM email_log")["status"] == "imported"


def test_duplicate_message_is_imported_once(cfg, jpeg):
    mailin.process_message(message(jpeg), cfg)
    assert mailin.process_message(message(jpeg), cfg)["status"] == "duplicate"
    assert len(uploads()) == 1


def test_listed_sender_without_account_owns_by_address(cfg, jpeg):
    raw = message(jpeg, sender="kiosk@partner.example", auth="mx.google.com; dkim=pass header.d=partner.example")
    assert mailin.process_message(raw, cfg)["imported"] == 1
    assert uploads()[0]["created_by"] == "kiosk@partner.example"


@pytest.mark.parametrize("sender,auth", [
    ("stranger@else.example", GMAIL_OK.replace("corp.example", "else.example")),             # not approved
    ("ana@corp.example", None),                                                                # no results header
    ("ana@corp.example", "evil.example; dmarc=pass header.from=corp.example"),                 # wrong server
    ("ana@corp.example", "mx.google.com; dmarc=fail header.from=corp.example"),                # failed
    ("ana@corp.example", "mx.google.com; dkim=pass header.d=evil.example; dmarc=none"),        # unaligned signer
    ("ana@corp.example", "mx.google.com; spf=softfail (domain of dmarc=pass@x designates) smtp.mailfrom=dmarc=pass"),  # comment trick
])
def test_unapproved_or_unverified_mail_is_rejected(cfg, jpeg, sender, auth):
    assert mailin.process_message(message(jpeg, sender=sender, auth=auth), cfg)["status"] == "rejected"
    assert uploads() == []


def test_forged_header_below_the_servers_is_ignored(cfg, jpeg):
    m = EmailMessage()
    m["Authentication-Results"] = "mx.google.com; dmarc=fail header.from=corp.example"
    m["Authentication-Results"] = GMAIL_OK  # added by the sender, below the receiving server's header
    m["From"], m["Message-ID"] = "ana@corp.example", "<forged@x>"
    m.set_content("x")
    m.add_attachment(jpeg, maintype="image", subtype="jpeg", filename="a.jpg")
    assert mailin.process_message(m.as_bytes(), cfg)["status"] == "rejected"


@pytest.mark.parametrize("from_header", [
    "ana@corp.example <mallory@corp.example>",
    "<ana@corp.example>mallory@corp.example",
])
def test_malformed_from_is_rejected(cfg, jpeg, from_header):
    raw = message(jpeg).replace(b"From: ana@corp.example", b"From: " + from_header.encode())
    assert mailin.process_message(raw, cfg)["status"] == "rejected"
    assert uploads() == []


@pytest.mark.parametrize("auth", [
    'mx.google.com; spf=pass (google.com: domain of "a) ; dmarc=pass header.from=corp.example ("@evil.example) smtp.mailfrom=x; dmarc=fail header.from=corp.example',
    'mx.google.com; spf=pass smtp.mailfrom="x; dmarc=pass header.from=corp.example "@evil.example; dmarc=fail header.from=corp.example',
    'mx.google.com; dkim=pass header.d=sub.corp.example.evil.example; dmarc=fail header.from=corp.example',
    'mx.google.com; dmarc=pass header.from=corp.example (unbalanced',
])
def test_auth_results_tricks_are_rejected(cfg, jpeg, auth):
    assert mailin.process_message(message(jpeg, auth=auth), cfg)["status"] == "rejected"


def test_rejected_message_id_cannot_block_the_real_one(cfg, jpeg):
    mailin.process_message(message(jpeg, sender="mallory@else.example", auth=None), cfg)
    assert mailin.process_message(message(jpeg), cfg)["imported"] == 1


def test_signed_mail_sent_elsewhere_is_not_replayed(cfg, jpeg):
    raw = message(jpeg).replace(b"To: inbox@gmail.com", b"To: attacker@evil.example")
    assert mailin.process_message(raw, cfg)["status"] == "rejected"


def test_account_with_email_in_off_overrides_settings_list(cfg, jpeg):
    db.execute("UPDATE users SET email_in=0")
    cfg["senders"] = ["ana@corp.example"]
    assert mailin.process_message(message(jpeg), cfg)["status"] == "rejected"


def test_multi_attachment_titles_keep_subject(cfg, jpeg):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"], m["Authentication-Results"] = "ana@corp.example", "inbox@gmail.com", "Week", "<multi@x>", GMAIL_OK
    m.set_content("x")
    for n in ("one.jpg", "two.jpg"):
        m.add_attachment(jpeg, maintype="image", subtype="jpeg", filename=n)
    assert mailin.process_message(m.as_bytes(), cfg)["imported"] == 2
    assert sorted(u["title"] for u in uploads()) == ["Week: one", "Week: two"]


def test_server_change_clears_saved_password(admin):
    admin.patch("/api/mail", json={"username": "inbox@gmail.com", "password": "secret"}, headers=H)
    assert admin.get("/api/mail").json()["password_set"]
    assert admin.patch("/api/mail", json={"host": "imap.attacker.example"}, headers=H).status_code == 200
    assert not admin.get("/api/mail").json()["password_set"]
    admin.patch("/api/mail", json={"password": "secret"}, headers=H)
    assert admin.patch("/api/mail", json={"enabled": True, "username": "other@gmail.com"}, headers=H).status_code == 400


def test_verification_can_be_turned_off(cfg, jpeg):
    cfg["require_verified"] = False
    assert mailin.process_message(message(jpeg, auth=None), cfg)["imported"] == 1


def test_signature_logo_skipped_and_bad_files_reported(cfg, jpeg):
    assert mailin.process_message(message(jpeg, inline_logo=True), cfg)["imported"] == 1
    raw = message(b"not really a jpeg", msgid="<m2@corp.example>", name="fake.jpg")
    assert mailin.process_message(raw, cfg)["status"] == "rejected"
    raw = message(jpeg, msgid="<m3@corp.example>", name="run.exe")
    assert mailin.process_message(raw, cfg)["status"] == "rejected"
    details = [r["detail"] for r in db.rows("SELECT detail FROM email_log ORDER BY id")]
    assert "unsupported file type" in details[2]
    assert len(uploads()) == 1


class FakeIMAP:
    def __init__(self, messages):
        self.messages, self.seen = messages, set()

    def login(self, user, password):
        if password != "app-pass":
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    def select(self, folder):
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b" ".join(k for k in self.messages if k not in self.seen)]
        if cmd == "FETCH" and args[1] == "(RFC822.SIZE)":
            return "OK", [b"%s (RFC822.SIZE %d)" % (args[0], len(self.messages[args[0]]))]
        if cmd == "FETCH":
            return "OK", [(b"1 (BODY[] {n}", self.messages[args[0]]), b")"]
        if cmd == "STORE":
            self.seen.add(args[0])
            return "OK", []

    def logout(self):
        pass


def test_poll_imports_marks_read_and_reports_sign_in_errors(admin, cfg, jpeg, monkeypatch):
    r = admin.patch("/api/mail", json={"enabled": True, "username": "inbox@gmail.com", "password": "app pass"}, headers=H)
    assert r.status_code == 200 and r.json()["password_set"] and "password" not in r.json()
    fake = FakeIMAP({b"7": message(jpeg), b"8": message(jpeg, sender="nobody@else.example", msgid="<z@else>")})
    monkeypatch.setattr(mailin, "_connect", lambda c: (fake.login(c["username"], mailin._password(c)), fake)[1])
    db.execute("UPDATE settings SET value=json_set(value,'$.senders',json('[]')) WHERE key='mail_in'")
    # App password was saved without spaces and still decrypts.
    assert mailin._password(mailin.load()) == "apppass"
    monkeypatch.setattr(mailin, "_password", lambda c: "app-pass")
    r = admin.post("/api/mail/check", headers=H).json()
    assert r["ok"] and r["checked"] == 2 and r["imported"] == 1 and fake.seen == {b"7", b"8"}
    assert [x["status"] for x in r["mail"]["recent"]] == ["rejected", "imported"]
    assert admin.get("/api/overview").json()["mail_in"] == {"enabled": True, "address": "inbox@gmail.com"}

    monkeypatch.setattr(mailin, "_password", lambda c: "wrong")
    r = admin.post("/api/mail/check", headers=H).json()
    assert not r["ok"] and "app password" in r["error"]


@pytest.mark.parametrize("body", [
    {"enabled": True},                                  # no mailbox or password yet
    {"username": "not-an-email"},
    {"host": "https://imap.gmail.com"},
    {"poll_minutes": 3},
    {"host": "10.0.0.5"},
    {"host": "localhost"},
    {"port": 25},
    {"folder": "Signage-é"},
    {"senders": ["ok@a.example", "bad"]},
    {"unknown": 1},
])
def test_mail_settings_validation(admin, body):
    assert admin.patch("/api/mail", json=body, headers=H).status_code == 400


def test_user_email_fields(admin):
    r = admin.post("/api/users", json={"username": "bo", "password": "a-long-password-123", "email": "Bo@Corp.example", "email_in": True}, headers=H)
    assert r.status_code == 200, r.text
    bo = next(u for u in admin.get("/api/users").json() if u["username"] == "bo")
    assert bo["email"] == "bo@corp.example" and bo["email_in"] == 1
    admin_id = next(u["id"] for u in admin.get("/api/users").json() if u["username"] == "admin")
    assert admin.patch(f"/api/users/{admin_id}", json={"email": "bo@corp.example"}, headers=H).status_code == 409
    assert admin.patch(f"/api/users/{admin_id}", json={"email_in": True}, headers=H).status_code == 400
    assert admin.patch(f"/api/users/{bo['id']}", json={"email": ""}, headers=H).status_code == 400  # email-in still on
    assert admin.patch(f"/api/users/{bo['id']}", json={"email": "", "email_in": False}, headers=H).status_code == 200


def test_mail_api_requires_sign_in(client):
    assert client.get("/api/mail").status_code == 401
    assert client.post("/api/mail/check", headers=H).status_code == 401
