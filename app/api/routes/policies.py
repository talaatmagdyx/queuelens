"""Replay policies (app/application/replay_policies.py). They move messages with no person
in the loop, so only Admins create, change, run or re-enable one; Operators can see them,
preview a run, and pause one."""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.platform import _audit_change
from app.application.queue_service import UnsafeToBrowse
from app.auth.basic import CurrentUser, get_current_username, require_admin, require_operator

router = APIRouter(prefix="/api/policies", tags=["policies"])


class PolicyBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    queue: str = Field(min_length=1, max_length=255)
    max_deaths: int = Field(default=3, ge=1, le=100)  # at this many deaths: park
    backoff_minutes: int = Field(default=5, ge=1, le=1440)  # x 2^(deaths - 1)
    interval_minutes: int = Field(default=10, ge=1, le=1440)
    cap: int = Field(default=100, ge=1, le=1000)  # and never above the bulk limit
    enabled: bool = True


class PolicyToggle(BaseModel):
    enabled: bool


def _with_next_run(policy: dict[str, Any]) -> dict[str, Any]:
    last = policy["last_run_at"]
    next_at = (datetime.fromisoformat(last) + timedelta(minutes=policy["interval_minutes"])
               if last else None)
    return {**policy, "next_run_at": next_at.isoformat() if next_at and policy["enabled"]
            else None}


async def _found(request: Request, policy_id: int) -> dict[str, Any]:
    policy = await request.app.state.replay_policies.get(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Replay policy not found")
    return dict(policy)


async def _queue_exists(request: Request, queue: str) -> None:
    try:
        await request.app.state.queue_service.get_queue(queue)
    except Exception as error:
        raise HTTPException(status_code=404, detail=f"No queue named {queue}") from error


@router.get("")
async def list_policies(
    request: Request, _username: str = Depends(get_current_username)
) -> dict[str, Any]:
    return {"policies": [_with_next_run(p) for p in await request.app.state.replay_policies.list()]}


@router.post("")
async def create_policy(
    request: Request, body: PolicyBody, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    await _queue_exists(request, body.queue)
    policy = await request.app.state.replay_policies.create(admin.username, **body.model_dump())
    await _audit_change(request, admin, "create_replay_policy",
                        {"policy": policy["id"], "name": body.name, "queue": body.queue})
    return _with_next_run(policy)


@router.put("/{policy_id}")
async def update_policy(
    request: Request, policy_id: int, body: PolicyBody,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    await _found(request, policy_id)
    await _queue_exists(request, body.queue)
    policy = await request.app.state.replay_policies.update(policy_id, **body.model_dump())
    await _audit_change(request, admin, "update_replay_policy",
                        {"policy": policy_id, "name": body.name, "queue": body.queue})
    return _with_next_run(dict(policy or {}))


@router.patch("/{policy_id}")
async def toggle_policy(
    request: Request, policy_id: int, body: PolicyToggle,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    """Anyone who can act may pause a policy; turning one back on is an Admin's call."""
    if body.enabled and not user.is_admin:
        raise HTTPException(
            status_code=403, detail="Enabling a replay policy requires the Admin role"
        )
    current = await _found(request, policy_id)
    policy = await request.app.state.replay_policies.update(policy_id, enabled=body.enabled)
    await _audit_change(request, user, "update_replay_policy",
                        {"policy": policy_id, "name": current["name"], "enabled": body.enabled})
    return _with_next_run(dict(policy or {}))


@router.delete("/{policy_id}")
async def delete_policy(
    request: Request, policy_id: int, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    current = await _found(request, policy_id)
    await request.app.state.replay_policies.delete(policy_id)
    await _audit_change(request, admin, "delete_replay_policy",
                        {"policy": policy_id, "name": current["name"]})
    return {"deleted": policy_id}


@router.post("/{policy_id}/preview")
async def preview_policy(
    request: Request, policy_id: int, _user: CurrentUser = Depends(require_operator)
) -> dict[str, Any]:
    """What a run would do right now: reads the DLQ once, moves nothing."""
    policy = await _found(request, policy_id)
    try:
        return dict(await request.app.state.policy_runner.run(policy, preview=True))
    except UnsafeToBrowse as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{policy_id}/run")
async def run_policy(
    request: Request, policy_id: int, _admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    policy = await _found(request, policy_id)
    try:
        return dict(await request.app.state.policy_runner.run(policy))
    except UnsafeToBrowse as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
