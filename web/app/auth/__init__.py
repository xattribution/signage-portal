"""
Auth chain. AUTH_MODE is a comma-separated list; a request must pass every provider.
The identity shown in the portal comes from the last provider in the chain (so with
"cloudflare,local" the portal username is shown, and the Cloudflare email is still
required at the edge).

To add a provider (e.g. OIDC): subclass AuthProvider in a new module, add it to
PROVIDERS below, and set AUTH_MODE=oidc. Nothing else changes.
"""
from fastapi import Depends, HTTPException, Request

from .. import config
from ..security import check_write
from .base import AuthProvider, Principal
from .cloudflare import CloudflareAuth
from .local import LocalAuth


class NoAuth(AuthProvider):
    """Login-free mode (AUTH_MODE=none): access control is handled outside the portal,
    for example by a reverse proxy, Cloudflare Access or network segmentation."""
    name = "none"

    def identify(self, request: Request) -> Principal:
        return Principal(username="open-access", provider=self.name)


PROVIDERS: dict[str, type[AuthProvider]] = {
    "local": LocalAuth,
    "cloudflare": CloudflareAuth,
    "none": NoAuth,
}

chain: list[AuthProvider] = []


def init() -> None:
    chain.clear()
    for mode in config.AUTH_MODE:
        if mode not in PROVIDERS:
            raise RuntimeError(f"Unknown AUTH_MODE '{mode}'. Options: {', '.join(PROVIDERS)}")
        chain.append(PROVIDERS[mode]())
    if any(p.name == "none" for p in chain):
        print("[auth] Login-free mode (AUTH_MODE=none): anyone who can reach this portal can manage it. "
              "Control access in front of it.", flush=True)


def identify(request: Request) -> tuple[Principal | None, AuthProvider | None]:
    """Returns (principal, None) on success or (None, failing_provider)."""
    who = None
    for p in chain:
        got = p.identify(request)
        if got is None:
            return None, p
        who = got
    return who, None


def manages_users() -> bool:
    return any(p.manages_users for p in chain)


def require_user(request: Request) -> Principal:
    who, _ = identify(request)
    if who is None:
        raise HTTPException(401, "Not authenticated")
    check_write(request)
    return who


CurrentUser = Depends(require_user)
