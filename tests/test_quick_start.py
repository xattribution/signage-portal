"""First-launch setup, portal settings, automatic host handling and cookie mode."""
import pytest
from fastapi.testclient import TestClient

from app import config, db, settings
from app.auth import local
from app.main import app

WRITE = {"X-Signage": "1"}
SETUP = {"site_name": "Lobby Screens", "username": "owner", "password": "a-long-setup-password"}


@pytest.fixture
def fresh(monkeypatch):
    """A brand-new install: no accounts yet."""
    db.execute("DELETE FROM users")
    monkeypatch.setattr(config, "SETUP_TOKEN", "")
    return TestClient(app, base_url="http://localhost", client=("192.168.1.20", 50000))


def test_first_launch_redirects_to_setup(fresh):
    assert fresh.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert fresh.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert fresh.get("/setup").status_code == 200
    status = fresh.get("/api/setup").json()
    assert status["needed"] is True and status["lan"] is True and status["code_required"] is False


def test_setup_creates_account_signs_in_and_closes(fresh, monkeypatch):
    monkeypatch.setattr(config, "SITE_NAME", "Signage")
    response = fresh.post("/api/setup", json=SETUP, headers=WRITE)
    assert response.status_code == 200, response.text
    assert fresh.get("/api/overview").status_code == 200
    assert config.SITE_NAME == "Lobby Screens"
    assert db.row("SELECT username FROM users")["username"] == "owner"
    # Setup can never run a second time.
    again = fresh.post("/api/setup", json={**SETUP, "username": "intruder"}, headers=WRITE)
    assert again.status_code == 409
    assert fresh.get("/setup", follow_redirects=False).headers["location"] == "/"
    assert db.row("SELECT COUNT(*) AS n FROM users")["n"] == 1


def test_setup_requires_lan_client(fresh):
    outside = TestClient(app, base_url="http://localhost", client=("8.8.8.8", 50000))
    assert outside.post("/api/setup", json=SETUP, headers=WRITE).status_code == 403
    assert db.row("SELECT COUNT(*) AS n FROM users")["n"] == 0


def test_setup_rejects_weak_password_and_missing_header(fresh):
    assert fresh.post("/api/setup", json={**SETUP, "password": "short"}, headers=WRITE).status_code == 400
    assert fresh.post("/api/setup", json=SETUP).status_code == 403


def test_setup_code_when_configured(fresh, monkeypatch):
    monkeypatch.setattr(config, "SETUP_TOKEN", "office-setup-code")
    assert fresh.get("/api/setup").json()["code_required"] is True
    bad = fresh.post("/api/setup", json={**SETUP, "setup_code": "wrong-code"}, headers=WRITE)
    assert bad.status_code == 401
    good = fresh.post("/api/setup", json={**SETUP, "setup_code": "office-setup-code"}, headers=WRITE)
    assert good.status_code == 200, good.text


def test_settings_round_trip_and_audit(admin, monkeypatch):
    for key in ("SITE_NAME", "CLOCK_24H", "DEFAULT_SLIDE_SECONDS", "TARGET_WIDTH", "TARGET_HEIGHT"):
        monkeypatch.setattr(config, key, getattr(config, key))
    data = admin.get("/api/settings").json()
    assert data["values"]["render_size"] == "1080p"
    saved = admin.patch("/api/settings", json={"site_name": "Front Lobby", "clock_24h": False,
                                               "default_slide_seconds": 15, "render_size": "4k"}, headers=WRITE)
    assert saved.status_code == 200, saved.text
    assert (config.SITE_NAME, config.CLOCK_24H, config.DEFAULT_SLIDE_SECONDS) == ("Front Lobby", False, 15)
    assert (config.TARGET_WIDTH, config.TARGET_HEIGHT) == (3840, 2160)
    assert admin.get("/api/site").json()["name"] == "Front Lobby"
    # Stored values survive a restart (db.init reloads them).
    config.SITE_NAME = "Signage"
    db.init()
    assert config.SITE_NAME == "Front Lobby"
    assert "changed settings" in db.row("SELECT what FROM audit ORDER BY id DESC LIMIT 1")["what"]


@pytest.mark.parametrize("body", [{"site_name": ""}, {"default_slide_seconds": 1}, {"render_size": "8k"},
                                  {"clock_24h": "yes"}, {"unknown": 1}, {}])
def test_settings_validation(admin, body):
    assert admin.patch("/api/settings", json=body, headers=WRITE).status_code == 400


def test_env_locked_settings(admin, monkeypatch):
    monkeypatch.setenv("SITE_NAME", "From Environment")
    assert admin.get("/api/settings").json()["locked"]["site_name"] == ["SITE_NAME"]
    assert admin.patch("/api/settings", json={"site_name": "Changed"}, headers=WRITE).status_code == 409


def test_settings_require_sign_in(client):
    assert client.get("/api/settings").status_code == 401
    assert client.patch("/api/settings", json={"site_name": "x"}, headers=WRITE).status_code == 401


def test_auto_hosts_accept_ip_literals_and_listed_names(admin, monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS")  # A quick-start install has no ALLOWED_HOSTS.
    monkeypatch.setattr(config, "HOSTS_AUTO", True)
    monkeypatch.setattr(config, "EXTRA_HOSTS", [])
    assert admin.get("/healthz", headers={"Host": "192.168.1.50:51480"}).status_code == 200
    assert admin.get("/healthz", headers={"Host": "[fd00::5]:51480"}).status_code == 200
    rejected = admin.get("/healthz", headers={"Host": "signage.office.lan:51480"})
    assert rejected.status_code == 400 and "Access names" in rejected.json()["detail"]
    saved = admin.patch("/api/settings", json={"access_names": ["Signage.Office.LAN"]}, headers=WRITE)
    assert saved.status_code == 200, saved.text
    assert admin.get("/healthz", headers={"Host": "signage.office.lan:51480"}).status_code == 200
    assert admin.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
    for bad in (["http://x.lan"], ["x.lan:80"], ["bad name"], ["*.lan"]):
        assert admin.patch("/api/settings", json={"access_names": bad}, headers=WRITE).status_code == 400


def test_explicit_allowed_hosts_lock_access_names(admin):
    # The test environment sets ALLOWED_HOSTS, so the list is managed by the server.
    assert settings.locked("access_names")
    assert admin.patch("/api/settings", json={"access_names": ["x.lan"]}, headers=WRITE).status_code == 409


def test_auto_cookie_follows_request_scheme(client, monkeypatch):
    monkeypatch.setattr(config, "COOKIE_AUTO", True)
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    login = {"username": "admin", "password": "test-password-only-123"}
    plain = client.post("/auth/login", json=login, headers=WRITE).headers["set-cookie"]
    assert plain.startswith("sp_session=") and "secure" not in plain.lower()
    tls = TestClient(app, base_url="https://localhost")
    secure = tls.post("/auth/login", json=login, headers=WRITE)
    assert secure.headers["set-cookie"].startswith("__Host-sp_session=") and "secure" in secure.headers["set-cookie"].lower()
    assert "strict-transport-security" in secure.headers
    assert tls.get("/api/overview").status_code == 200


def test_auto_cookie_mode_is_not_an_insecure_override(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_INSECURE_DEV", False)
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "COOKIE_AUTO", True)
    config.validate()
    monkeypatch.setattr(config, "COOKIE_AUTO", False)
    with pytest.raises(RuntimeError):
        config.validate()
