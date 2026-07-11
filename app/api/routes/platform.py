"""Platform APIs: settings, alert rules, notifications, users, environments."""

import secrets
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.basic import get_current_username

router = APIRouter(prefix="/api", tags=["platform"])

# ---------------------------------------------------------------- settings

ALLOWED_SETTING_KEYS = {"custom_headers", "channels", "limits", "retention", "ui"}


SECRET_SENTINEL = "__secret__"


def _redact_channels(settings: dict[str, Any]) -> dict[str, Any]:
    channels = settings.get("channels")
    if isinstance(channels, dict):
        email = channels.get("email")
        if isinstance(email, dict) and email.get("password"):
            redacted = {**email, "password": SECRET_SENTINEL}
            settings = {**settings, "channels": {**channels, "email": redacted}}
    stored_envs = settings.get("custom_environments")
    if isinstance(stored_envs, dict):
        cleaned = {
            name: {
                **profile,
                **(
                    {"management_password": SECRET_SENTINEL}
                    if (profile or {}).get("management_password")
                    else {}
                ),
                **({"rabbitmq_url": "__redacted__"} if (profile or {}).get("rabbitmq_url") else {}),
            }
            for name, profile in stored_envs.items()
        }
        settings = {**settings, "custom_environments": cleaned}
    return settings


@router.get("/settings")
async def get_settings_api(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    return _redact_channels(
        cast(dict[str, Any], await request.app.state.settings_store.get_all())
    )


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


@router.put("/settings")
async def put_settings_api(
    request: Request,
    body: SettingsUpdate,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    unknown = set(body.values) - ALLOWED_SETTING_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown settings keys: {sorted(unknown)}")
    values = body.values
    if "ui" in values:
        request.app.state.audit_repository.stream_to_log = bool(
            (values.get("ui") or {}).get("syslog")
        )
    email = ((values.get("channels") or {}).get("email")) if "channels" in values else None
    if isinstance(email, dict) and email.get("password") == SECRET_SENTINEL:
        stored = await request.app.state.settings_store.get("channels", {}) or {}
        email["password"] = (stored.get("email") or {}).get("password", "")
    return _redact_channels(
        cast(dict[str, Any], await request.app.state.settings_store.put(values))
    )


# ---------------------------------------------------------------- alert rules

CHANNELS = ("email", "slack", "webhook", "pagerduty")


class AlertRuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    pattern: str = Field(default="*", min_length=1, max_length=255)
    metric: Literal["messages_ready", "messages", "consumers", "publish_rate"] = "messages_ready"
    operator: Literal[">", ">=", "=", "<"] = ">"
    threshold: int = 100
    duration_seconds: int = Field(default=0, ge=0, le=86_400)
    severity: Literal["Info", "Warning", "Alert"] = "Warning"
    channels: list[Literal["email", "slack", "webhook", "pagerduty"]] = []
    enabled: bool = True


@router.get("/alerts")
async def list_alerts(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    return {"rules": await request.app.state.alert_rules.list()}


@router.post("/alerts")
async def create_alert(
    request: Request,
    body: AlertRuleBody,
    username: str = Depends(get_current_username),
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        await request.app.state.alert_rules.create(created_by=username, **body.model_dump()),
    )


@router.put("/alerts/{rule_id}")
async def update_alert(
    request: Request,
    rule_id: int,
    body: AlertRuleBody,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    updated = await request.app.state.alert_rules.update(rule_id, **body.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return cast(dict[str, Any], updated)


class AlertPatch(BaseModel):
    enabled: bool


@router.patch("/alerts/{rule_id}")
async def patch_alert(
    request: Request,
    rule_id: int,
    body: AlertPatch,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    updated = await request.app.state.alert_rules.update(rule_id, enabled=body.enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return cast(dict[str, Any], updated)


@router.delete("/alerts/{rule_id}")
async def delete_alert(
    request: Request,
    rule_id: int,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    if not await request.app.state.alert_rules.delete(rule_id):
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return {"deleted": rule_id}


class ChannelTest(BaseModel):
    channel: Literal["email", "slack", "webhook", "pagerduty"]


@router.post("/alerts/test-channel")
async def test_channel(
    request: Request,
    body: ChannelTest,
    username: str = Depends(get_current_username),
) -> dict[str, Any]:
    """Send a test notification through one channel; returns the delivery outcome."""
    engine = request.app.state.alert_engine
    outcome = await engine.dispatch(
        [body.channel],
        "Test notification",
        f"Channel test triggered by {username} — if you can read this, delivery works.",
    )
    return cast(dict[str, Any], outcome.get(body.channel, {}))


# ---------------------------------------------------------------- notifications


@router.get("/notifications")
async def list_notifications(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    return {"notifications": await request.app.state.notifications.list(limit=100)}


# ---------------------------------------------------------------- users


class InviteBody(BaseModel):
    username: str = Field(min_length=2, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    role: Literal["Admin", "Operator", "Viewer"] = "Operator"
    email: str | None = Field(default=None, max_length=255)


@router.post("/users/invite")
async def invite_user(
    request: Request,
    body: InviteBody,
    username: str = Depends(get_current_username),
) -> dict[str, Any]:
    password = secrets.token_urlsafe(12)
    created = await request.app.state.users.create(
        username=body.username,
        password=password,
        role=body.role,
        email=body.email,
        invited_by=username,
    )
    if not created:
        raise HTTPException(status_code=409, detail="User already exists")
    email_result: dict[str, Any] | None = None
    if body.email:
        channels = await request.app.state.settings_store.get("channels", {}) or {}
        email_config = dict(channels.get("email") or {})
        if email_config.get("smtp_host"):
            email_config["to"] = body.email
            from app.infrastructure.mailer import send_email

            email_result = await send_email(
                email_config,
                "[QueueLens] You have been invited",
                (
                    f"{username} invited you to QueueLens as {body.role}.\n\n"
                    f"Username: {body.username}\nPassword: {password}\n\n"
                    "Sign in with HTTP Basic auth and change your password with an admin."
                ),
            )
    return {
        "username": body.username,
        "role": body.role,
        "password": password,  # shown exactly once
        "email_delivery": email_result,
    }


# ---------------------------------------------------------------- environments


@router.get("/environments")
async def list_environments(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    return {"environments": request.app.state.environment_manager.list()}


class EnvironmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    vhosts: list[str] = Field(min_length=1, max_length=50)
    # optional full broker profile — blank fields inherit the default environment
    host: str | None = Field(default=None, max_length=255)  # e.g. rabbitmq-stg:5672
    username: str | None = Field(default=None, max_length=128)  # AMQP user
    password: str | None = Field(default=None, max_length=255)  # AMQP password
    management_url: str | None = Field(default=None, max_length=255)
    # management credentials fall back to the AMQP ones when omitted
    management_username: str | None = Field(default=None, max_length=128)
    management_password: str | None = Field(default=None, max_length=255)


@router.post("/environments")
async def create_environment(
    request: Request,
    body: EnvironmentBody,
    username: str = Depends(get_current_username),
) -> dict[str, Any]:
    """Create a same-broker environment or add vhosts to an existing one.
    Environments with their own broker/credentials belong in
    QUEUELENS_ENVIRONMENTS_JSON — credentials never pass through this API."""
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    vhosts = [v.strip() for v in body.vhosts if v.strip()]
    if not vhosts:
        raise HTTPException(status_code=400, detail="At least one vhost is required")
    store = request.app.state.settings_store
    stored = await store.get("custom_environments", {}) or {}
    previous = stored.get(body.name, {}) or {}
    merged = sorted(set(previous.get("vhosts", [])) | set(vhosts))
    profile: dict[str, Any] = {**previous, "vhosts": merged}
    if body.host:
        username = body.username or ""
        password = body.password or ""
        credentials = f"{username}:{password}@" if username else ""
        profile["rabbitmq_url"] = f"amqp://{credentials}{body.host.strip()}/"
    if body.management_url:
        profile["management_url"] = body.management_url.strip()
    mgmt_user = body.management_username or body.username
    mgmt_pass = body.management_password or body.password
    if mgmt_user:
        profile["management_username"] = mgmt_user
    if mgmt_pass and mgmt_pass != SECRET_SENTINEL:
        profile["management_password"] = mgmt_pass
    stored[body.name] = profile
    await store.put({"custom_environments": stored})
    request.app.state.environment_manager.apply_custom(stored)
    await request.app.state.audit_repository.record(
        AuditEntry(
            username=username,
            action="add_environment",
            timestamp=datetime.now(UTC),
            result="success",
            metadata={"name": body.name, "vhosts": vhosts},
        )
    )
    return {"environments": request.app.state.environment_manager.list()}


@router.delete("/environments/{name}")
async def delete_environment(
    request: Request,
    name: str,
    username: str = Depends(get_current_username),
) -> dict[str, Any]:
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    try:
        request.app.state.environment_manager.remove_custom(name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    store = request.app.state.settings_store
    stored = await store.get("custom_environments", {}) or {}
    stored.pop(name, None)
    await store.put({"custom_environments": stored})
    await request.app.state.audit_repository.record(
        AuditEntry(
            username=username,
            action="remove_environment",
            timestamp=datetime.now(UTC),
            result="success",
            metadata={"name": name},
        )
    )
    return {"environments": request.app.state.environment_manager.list()}


class ActivateBody(BaseModel):
    environment: str = Field(min_length=1)
    vhost: str | None = None


@router.post("/environments/activate")
async def activate_environment(
    request: Request,
    body: ActivateBody,
    username: str = Depends(get_current_username),
) -> dict[str, Any]:
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    try:
        result = await request.app.state.environment_manager.activate(
            body.environment, body.vhost
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ConnectionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    await request.app.state.audit_repository.record(
        AuditEntry(
            username=username,
            action="switch_environment",
            timestamp=datetime.now(UTC),
            result="success",
            metadata=result,
        )
    )
    return cast(dict[str, Any], result)
