import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from app import auth, config, db
from app.auth import local
from app.security import BoundaryMiddleware

WRITE = {"X-Signage": "1"}
LOGIN = {"username": "admin", "password": "test-password-only-123"}

def test_login_requires_guard(client):
    assert client.post("/auth/login", json=LOGIN).status_code == 403

@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}])
def test_login_rejects_cross_site(client, headers):
    assert client.post("/auth/login", json=LOGIN, headers={**WRITE, **headers}).status_code == 403

@pytest.mark.parametrize("host", ["evil.example", "localhost@evil.example", "localhost:bad", "localhost/evil", "localhost\\evil"])
def test_host_validation(client, host):
    assert client.get("/healthz", headers={"Host": host}).status_code == 400

def test_opaque_session_and_logout_revocation(admin):
    token = admin.cookies.get(local.COOKIE)
    assert token and len(token) == 43
    rows = db.rows("SELECT * FROM sessions")
    assert rows[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(rows)
    response = admin.post("/auth/logout", headers=WRITE)
    assert response.status_code == 200
    assert admin.get("/api/overview", headers={"Cookie": f"{local.COOKIE}={token}"}).status_code == 401
    assert admin.get("/logout").status_code == 405

def test_cookie_policy(admin):
    response = admin.post("/auth/login", json=LOGIN, headers=WRITE)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie

def test_password_change_revokes_session(admin):
    uid = db.row("SELECT id FROM users")["id"]
    assert admin.put(f"/api/users/{uid}/password", json={"password": "changed-password-123"}, headers=WRITE).status_code == 200
    assert admin.get("/api/overview").status_code == 401
    assert not db.rows("SELECT * FROM sessions")

def test_argon2_and_whitespace():
    value = "  valid password  "
    hashed = local.hash_password(value)
    assert hashed.startswith("$argon2id$")
    assert local.check_password(value, hashed)
    assert not local.check_password(value.strip(), hashed)

def test_legacy_bcrypt_upgrade(client):
    bcrypt = pytest.importorskip("bcrypt", reason="Legacy migration requires bcrypt wheel unavailable in this environment")
    hashed = bcrypt.hashpw(LOGIN["password"].encode(), bcrypt.gensalt()).decode()
    db.execute("UPDATE users SET password_hash=?", (hashed,))
    assert client.post("/auth/login", json=LOGIN, headers=WRITE).status_code == 200
    assert db.row("SELECT password_hash FROM users")["password_hash"].startswith("$argon2id$")

def test_persistent_concurrent_throttle():
    def attempt(i):
        try:
            local._reserve_attempt("target", f"192.0.2.{i}")
            return True
        except HTTPException as error:
            assert error.status_code == 429
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(12)))
    assert sum(results) == 8
    assert db.rows("SELECT * FROM auth_attempts")
    with pytest.raises(HTTPException): local._reserve_attempt("target", "198.51.100.20")

def test_session_expiration(admin):
    db.execute("UPDATE sessions SET expires_at=0")
    assert admin.get("/api/overview").status_code == 401

def test_validation_redacts_secrets(admin):
    response = admin.post("/api/users", json={"username": "new", "password": "private-secret" * 100}, headers=WRITE)
    assert response.status_code == 422
    assert "private-secret" not in response.text

def test_production_settings_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_INSECURE_DEV", False)
    with pytest.raises(RuntimeError): config.validate()
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    config.validate()
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with pytest.raises(RuntimeError): config.validate()

def test_security_headers(client):
    response = client.get("/login")
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] in {"no-cache", "no-store"}

def test_body_bound_before_parser(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    async def downstream(scope, receive, send):
        try:
            await Request(scope, receive).body()
            response = JSONResponse({"ok": True})
        except StarletteHTTPException as error:
            response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
        await response(scope, receive, send)
    async def run():
        messages = []
        sent = False
        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"x" * (1048576 + 65537), "more_body": False}
            return {"type": "http.disconnect"}
        async def send(message): messages.append(message)
        scope = {"type": "http", "path": "/api/uploads", "method": "POST", "scheme": "http", "headers": [(b"host",b"localhost"),(b"x-signage",b"1")], "query_string": b""}
        await BoundaryMiddleware(downstream)(scope, receive, send)
        assert next(x for x in messages if x["type"] == "http.response.start")["status"] == 413
    asyncio.run(run())


def test_https_required_for_administration(client, monkeypatch):
    monkeypatch.setattr(config, 'COOKIE_SECURE', True)
    monkeypatch.setattr(config, 'ALLOW_INSECURE_DEV', False)
    assert client.get('/login').status_code == 400
    assert client.post('/auth/login', json=LOGIN, headers=WRITE).status_code == 400
    assert client.get('/api/overview').status_code == 400
    assert client.get('/healthz').status_code == 200
    assert client.get('/display/lobby-1').status_code == 200
    assert client.get('https://localhost/login').status_code == 200


def test_large_content_length_rejected_without_integer_exception(client):
    response = client.post('/auth/login', content=b'', headers={**WRITE, 'Content-Length':'9'*5000})
    assert response.status_code == 400


def test_mixed_wildcard_proxy_is_rejected(monkeypatch):
    monkeypatch.setenv('FORWARDED_ALLOW_IPS', '127.0.0.1,*')
    with pytest.raises(RuntimeError):
        config.validate()


def test_oversized_heartbeat_id_is_validation_error(client):
    response = client.get('/api/player/display/lobby-1?now=' + '9'*80)
    assert response.status_code == 422


def test_cloudflare_validates_required_claims(monkeypatch):
    import time
    from types import SimpleNamespace
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi import Request
    from app.auth.cloudflare import CloudflareAuth
    monkeypatch.setattr(config, 'CF_TEAM_DOMAIN', 'test-team.cloudflareaccess.com')
    monkeypatch.setattr(config, 'CF_AUD', 'test-audience')
    provider = CloudflareAuth()
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(provider._jwks, 'get_signing_key_from_jwt', lambda _: SimpleNamespace(key=private.public_key()))
    now = int(time.time())
    claims = {'exp': now+300, 'iat': now-1, 'sub': 'test-subject', 'aud': 'test-audience',
              'iss': provider.issuer, 'type': 'app', 'email': 'Admin@example.org'}
    def identify(data, key=private):
        token = jwt.encode(data, key, algorithm='RS256')
        return provider.identify(Request({'type':'http', 'headers':[(b'cf-access-jwt-assertion',token.encode())]}))
    assert identify(claims).username == 'admin@example.org'
    for missing in ('exp', 'iat', 'sub', 'aud', 'iss'):
        assert identify({k:v for k,v in claims.items() if k != missing}) is None
    assert identify({**claims, 'aud':'another-app'}) is None
    assert identify({**claims, 'exp':now-20}) is None
    assert identify({**claims, 'type':'service'}) is None
    assert identify(claims, rsa.generate_private_key(public_exponent=65537,key_size=2048)) is None
    monkeypatch.setattr(config, 'CF_ALLOWED_EMAILS', ['other@example.org'])
    assert identify(claims) is None
