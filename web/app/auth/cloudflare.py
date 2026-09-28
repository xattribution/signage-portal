"""
Cloudflare Access auth.

Cloudflare Access sits in front of the portal and injects a signed JWT on every
request (Cf-Access-Jwt-Assertion header, and a CF_Authorization cookie). We verify the
signature against the team's public keys, the audience tag, and the issuer, so a
request that bypassed Cloudflare (or forged the header) is rejected.
"""
import re

import jwt
from fastapi import Request

from .. import config
from .base import AuthProvider, Principal


class CloudflareAuth(AuthProvider):
    name = "cloudflare"

    def __init__(self) -> None:
        if not config.CF_TEAM_DOMAIN or not config.CF_AUD:
            raise RuntimeError("AUTH_MODE includes 'cloudflare' but CF_TEAM_DOMAIN / CF_AUD are not set.")
        team = config.CF_TEAM_DOMAIN.removeprefix("https://").rstrip("/")
        if not re.fullmatch(r"[a-z0-9-]+\.cloudflareaccess\.com", team):
            raise RuntimeError("CF_TEAM_DOMAIN must be a Cloudflare Access team hostname.")
        self.issuer = f"https://{team}"
        self._jwks = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=False, lifespan=300, timeout=5)

    def identify(self, request: Request) -> Principal | None:
        token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
        if not token or len(token) > 16384:
            return None
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=config.CF_AUD,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except Exception:
            return None
        email = claims.get("email")
        if not isinstance(email, str) or claims.get("type") != "app":
            return None
        email = email.lower()
        if not email:
            return None
        if config.CF_ALLOWED_EMAILS and email not in config.CF_ALLOWED_EMAILS:
            return None
        return Principal(username=email, provider=self.name)

    def logout(self, request, response) -> None:
        # Cloudflare owns this session; /cdn-cgi/access/logout ends it at the edge.
        return None
