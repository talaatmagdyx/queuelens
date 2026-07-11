"""HTTP Basic auth with role resolution and failed-attempt rate limiting."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from hmac import compare_digest

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)

# Sliding-window limiter for failed logins (per client IP). In-memory — QueueLens
# runs single-instance (see docs/OPERATIONS.md deployment constraints).
MAX_FAILURES = 10
WINDOW_SECONDS = 60
_failures: dict[str, deque[float]] = defaultdict(deque)


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: str  # Admin | Operator | Viewer

    @property
    def is_admin(self) -> bool:
        return self.role == "Admin"

    @property
    def can_operate(self) -> bool:
        return self.role in ("Admin", "Operator")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window = _failures[ip]
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MAX_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts; try again in a minute",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )


def _record_failure(ip: str) -> None:
    _failures[ip].append(time.monotonic())


async def get_current_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> CurrentUser:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return CurrentUser(username="local", role="Admin")
    ip = _client_ip(request)
    _check_rate_limit(ip)
    if credentials is None:
        raise _unauthorized()
    matched = False
    for username, password in settings.users.items():
        # compare every account to keep timing independent of username validity
        if compare_digest(credentials.username, username) and compare_digest(
            credentials.password, password
        ):
            matched = True
    if matched:
        role = "Admin" if credentials.username == settings.admin_username else "Operator"
        return CurrentUser(username=credentials.username, role=role)
    users = getattr(request.app.state, "users", None)
    if users is not None and await users.verify(credentials.username, credentials.password):
        stored = {u["username"]: u for u in await users.list()}
        role = stored.get(credentials.username, {}).get("role", "Operator")
        return CurrentUser(username=credentials.username, role=role)
    _record_failure(ip)
    raise _unauthorized()


async def get_current_username(
    user: CurrentUser = Depends(get_current_user),
) -> str:
    return user.username


async def require_operator(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can_operate:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewer accounts are read-only — ask an Admin for the Operator role",
        )
    return user


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the Admin role",
        )
    return user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Basic"},
    )
