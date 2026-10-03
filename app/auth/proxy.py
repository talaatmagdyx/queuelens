"""SSO behind an authenticating reverse proxy (docs/SSO.md).

The proxy (oauth2-proxy, Authelia behind Traefik, an SSO ingress) signs the user in and
names them in a header. QueueLens believes that header only from the proxy itself: the
TCP peer must be in QUEUELENS_TRUSTED_PROXIES. The peer is read before X-Forwarded-For
replaces the client address, so a forwarded address can never pass for the proxy.
"""

import ipaddress
import logging

from fastapi import HTTPException, Request, status
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import ROLES

logger = logging.getLogger(__name__)
PEER = "queuelens.peer"  # ASGI scope key: the TCP peer, before proxy headers apply


class KeepPeer:
    """Outermost middleware: remembers who actually connected."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            scope[PEER] = client[0] if client else None
        await self.app(scope, receive, send)


def trusted(host: str | None, proxies: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(entry, strict=False) for entry in proxies)


async def proxy_user(request: Request) -> tuple[str, str] | None:
    """(username, role) the proxy vouches for; None falls back to Basic auth."""
    settings = request.app.state.settings
    username = request.headers.get(settings.auth_proxy_header, "").strip()
    if not username:
        return None
    peer = request.scope.get(PEER)
    if not trusted(peer, settings.trusted_proxy_list):
        logger.warning("ignored %s from %s: not in QUEUELENS_TRUSTED_PROXIES",
                       settings.auth_proxy_header, peer)
        return None
    users = getattr(request.app.state, "users", None)
    account = {u["username"]: u for u in await users.list()}.get(username) if users else None
    if account is not None:  # a local account: its role and its active switch apply
        if not account["active"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is deactivated")
        return username, str(account["role"])
    header = settings.auth_proxy_groups_header
    groups = request.headers.get(header, "") if header else ""
    roles = settings.auth_proxy_roles
    mapped = [roles[group.strip()] for group in groups.split(",") if group.strip() in roles]
    role = max(mapped, key=ROLES.index) if mapped else settings.auth_proxy_default_role
    if not role:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Signed in, but in no group that has a QueueLens role")
    return username, role
