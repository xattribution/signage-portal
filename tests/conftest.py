"""Isolated local storage; never connect tests to a deployment database."""
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
_boot = tempfile.TemporaryDirectory(prefix="signage-test-bootstrap-")
os.environ.update(DATA_DIR=_boot.name + "/data", MEDIA_DIR=_boot.name + "/media",
                  ALLOW_INSECURE_DEV="true", COOKIE_SECURE="false", AUTH_MODE="local",
                  ADMIN_PASSWORD="test-password-only-123", ALLOWED_HOSTS="localhost,127.0.0.1,testserver",
                  INGEST_ENABLED="false", TRANSFERS_ENABLED="false")
from app import auth, config, db
from app.main import app
from fastapi.testclient import TestClient
_BOOT_HASH = db.row("SELECT password_hash FROM users LIMIT 1")["password_hash"]

@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": tmp_path / "data", "MEDIA_DIR": tmp_path / "media",
                       "DB_PATH": tmp_path / "data/signage.db", "ORIGINALS_DIR": tmp_path / "media/originals",
                       "WORK_DIR": tmp_path / "media/work", "RENDERED_DIR": tmp_path / "media/rendered"}.items():
        monkeypatch.setattr(config, key, value)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret-not-for-deployment-1234567890")
    db.init()
    db.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)", ("admin", _BOOT_HASH, db.now_ms()))
    # StaticFiles holds its directory at construction; route it into this fixture.
    mount = next(route for route in app.routes if getattr(route, "path", None) == "/media")
    monkeypatch.setattr(mount.app, "directory", str(config.RENDERED_DIR))
    monkeypatch.setattr(mount.app, "all_directories", [str(config.RENDERED_DIR)])
    return tmp_path

@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost") as client:
        yield client

@pytest.fixture
def admin(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "test-password-only-123"}, headers={"X-Signage": "1"})
    assert response.status_code == 200, response.text
    return client

@pytest.fixture
def jpeg():
    from PIL import Image
    buffer = io.BytesIO(); Image.new("RGB", (32, 24), (80, 90, 110)).save(buffer, format="JPEG")
    return buffer.getvalue()

@pytest.fixture
def uploaded(admin, jpeg):
    response = admin.post("/api/uploads", files={"file": ("test.jpg", jpeg, "image/jpeg")}, data={"title": "Test content"}, headers={"X-Signage": "1"})
    assert response.status_code == 201, response.text
    return db.row("SELECT * FROM uploads WHERE id=?", (response.json()["id"],))
