"""
Auth provider contract.

Every provider answers one question: "who is making this request?" The rest of the
app only ever sees a Principal, so adding OIDC/OAuth, LDAP, or a different SSO later
means writing one new class here and registering it in auth/__init__.py. Nothing in
the API routes, database, or UI needs to change.
"""
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response


@dataclass
class Principal:
    username: str  # stable identifier shown in the UI and written to the audit log
    provider: str  # which provider vouched for this user


class AuthProvider:
    name: str = "base"

    # True if this provider owns an interactive login step (a login page / redirect).
    interactive: bool = False

    # True if this provider stores users in the portal DB (enables the Users tab).
    manages_users: bool = False

    def router(self) -> APIRouter | None:
        """Extra routes the provider needs (login form post, OAuth callback, ...)."""
        return None

    def identify(self, request: Request) -> Principal | None:
        """Return the caller's identity, or None if not authenticated by this provider."""
        raise NotImplementedError

    def login_url(self, request: Request) -> str | None:
        """Where to send an unauthenticated browser, if this provider has a login step."""
        return None

    def logout(self, request: Request, response: Response) -> None:
        """Clear any session this provider owns."""
        return None
