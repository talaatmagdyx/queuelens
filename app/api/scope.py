"""Which environment and vhost a request targets.

Clients name it per request with two headers; without them a request targets the default
environment. Nothing is instance-global, so concurrent users (and tabs) can work against
different brokers at once."""

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.auth.basic import CurrentUser, get_current_user
from app.infrastructure.persistence.audit_repository import BROKER_SCOPE

ENVIRONMENT_HEADER = "X-QueueLens-Environment"
VHOST_HEADER = "X-QueueLens-Vhost"


def requested_scope(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get(ENVIRONMENT_HEADER), request.headers.get(VHOST_HEADER)


async def broker_scope(
    request: Request,
    _user: CurrentUser = Depends(get_current_user),  # authenticate before connecting anywhere
) -> None:
    """Resolve the request's broker services onto request.state.broker."""
    manager = request.app.state.environment_manager
    env, vhost = requested_scope(request)
    try:
        key = manager.scope(env, vhost)
        request.state.broker = await manager.resolve(*key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0])) from error
    BROKER_SCOPE.set(key)  # every audit row written for this request names it


def broker(request: Request) -> Any:
    """The services broker_scope resolved — an AttributeError here means the route's
    router is missing the broker_scope dependency (fails closed, never the default)."""
    return request.state.broker
