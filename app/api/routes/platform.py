"""Platform APIs: settings, alert rules, notifications, users, environments."""

import secrets
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.scope import requested_scope
from app.auth.basic import (
    CurrentUser,
    get_current_username,
    require_admin,
    require_operator,
)
from app.auth.basic import (
    get_current_user as get_current_user_dep,
)

router = APIRouter(prefix="/api", tags=["platform"])


@router.get("/me")
async def whoami(user: CurrentUser = Depends(get_current_user_dep)) -> dict[str, Any]:
    return {
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }

# ---------------------------------------------------------------- settings

ALLOWED_SETTING_KEYS = {"custom_headers", "channels", "limits", "retention", "ui"}


SECRET_SENTINEL = "__secret__"
# Write-only channel fields: a Slack/webhook URL or a PagerDuty routing key is a
# credential in itself (whoever holds it can post as you).
CHANNEL_SECRETS = {
    "email": ("password",),
    "slack": ("url",),
    "webhook": ("url",),
    "pagerduty": ("url", "routing_key"),
}


def _redact_channels(settings: dict[str, Any]) -> dict[str, Any]:
    channels = settings.get("channels")
    if isinstance(channels, dict):
        settings = {
            **settings,
            "channels": {
                name: {
                    **config,
                    **{f: SECRET_SENTINEL for f in CHANNEL_SECRETS.get(name, ()) if config.get(f)},
                }
                if isinstance(config, dict)
                else config
                for name, config in channels.items()
            },
        }
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


# audited with their new values; every other key only by name (channels and custom
# headers can carry credentials)
AUDITED_SETTING_VALUES = {"retention", "limits"}


@router.put("/settings")
async def put_settings_api(
    request: Request,
    body: SettingsUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    unknown = set(body.values) - ALLOWED_SETTING_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown settings keys: {sorted(unknown)}")
    values = body.values
    quiet_tz = (values.get("ui") or {}).get("quiet_tz") if "ui" in values else None
    if quiet_tz:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(str(quiet_tz))
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise HTTPException(
                status_code=400, detail=f"Unknown time zone: {quiet_tz}"
            ) from error
    if "ui" in values:
        request.app.state.audit_repository.stream_to_log = bool(
            (values.get("ui") or {}).get("syslog")
        )
    channels = values.get("channels") if "channels" in values else None
    if isinstance(channels, dict):
        stored = await request.app.state.settings_store.get("channels", {}) or {}
        for name, fields in CHANNEL_SECRETS.items():
            config = channels.get(name)
            if not isinstance(config, dict):
                continue
            for field in fields:
                if config.get(field) == SECRET_SENTINEL:  # unchanged → keep the stored secret
                    config[field] = (stored.get(name) or {}).get(field, "")
    store = request.app.state.settings_store
    changed = sorted([key for key in values if await store.get(key) != values[key]])
    saved = cast(dict[str, Any], await store.put(values))
    if changed:  # a shortened retention or a redirected channel leaves a trace
        await _audit_change(request, user, "update_settings", {
            "keys": changed,
            **{key: values[key] for key in changed if key in AUDITED_SETTING_VALUES},
        })
    return _redact_channels(saved)


# ---------------------------------------------------------------- alert rules

CHANNELS = ("email", "slack", "webhook", "pagerduty")


def _watches(rule: dict[str, Any]) -> dict[str, Any]:
    """Audit a rule change against the scope the rule watches, not the request's."""
    return {"environment": rule.get("environment"), "vhost": rule.get("vhost")}


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
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    try:  # a rule watches the environment and vhost it was created in
        environment, vhost = request.app.state.environment_manager.scope(*requested_scope(request))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0])) from error
    rule = await request.app.state.alert_rules.create(
        created_by=user.username, environment=environment, vhost=vhost, **body.model_dump()
    )
    await _audit_change(request, user, "create_alert_rule",
                        {"rule": rule["id"], "name": body.name, **_watches(rule)})
    return cast(dict[str, Any], rule)


@router.put("/alerts/{rule_id}")
async def update_alert(
    request: Request,
    rule_id: int,
    body: AlertRuleBody,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    updated = await request.app.state.alert_rules.update(rule_id, **body.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await _audit_change(request, user, "update_alert_rule",
                        {"rule": rule_id, "name": body.name, **_watches(updated)})
    return cast(dict[str, Any], updated)


class AlertPatch(BaseModel):
    enabled: bool


@router.patch("/alerts/{rule_id}")
async def patch_alert(
    request: Request,
    rule_id: int,
    body: AlertPatch,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    updated = await request.app.state.alert_rules.update(rule_id, enabled=body.enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await _audit_change(request, user, "update_alert_rule",
                        {"rule": rule_id, "name": updated["name"], "enabled": body.enabled,
                         **_watches(updated)})
    return cast(dict[str, Any], updated)


@router.delete("/alerts/{rule_id}")
async def delete_alert(
    request: Request,
    rule_id: int,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    rules = {rule["id"]: rule for rule in await request.app.state.alert_rules.list()}
    if not await request.app.state.alert_rules.delete(rule_id):
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await _audit_change(request, user, "delete_alert_rule",
                        {"rule": rule_id, "name": rules.get(rule_id, {}).get("name"),
                         **_watches(rules.get(rule_id, {}))})
    return {"deleted": rule_id}


class ChannelTest(BaseModel):
    channel: Literal["email", "slack", "webhook", "pagerduty"]


@router.post("/alerts/test-channel")
async def test_channel(
    request: Request,
    body: ChannelTest,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    username = user.username
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
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    username = user.username
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
    await _audit_change(
        request, user, "invite_user", {"user": body.username, "new_role": body.role}
    )
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
                # never the password: mail is stored and forwarded in clear — the admin
                # sees it once in the API response and hands it over out-of-band
                (
                    f"{username} invited you to QueueLens as {body.role}.\n\n"
                    f"Username: {body.username}\n\n"
                    f"{username} will give you your initial password directly — it is never "
                    "sent by email. After signing in, change it under Users → My Password."
                ),
            )
    return {
        "username": body.username,
        "role": body.role,
        "password": password,  # shown exactly once
        "email_delivery": email_result,
    }


class UserChange(BaseModel):
    role: Literal["Admin", "Operator", "Viewer"] | None = None
    active: bool | None = None


def _changeable(request: Request, admin: CurrentUser, username: str) -> None:
    if username == admin.username:
        # an Admin can't lock themselves out, or take away the rights that undo it
        raise HTTPException(status_code=400, detail="You can't change or remove your own account")
    if username in request.app.state.settings.users:
        raise HTTPException(
            status_code=400,
            detail=f"{username} is set by environment variables (QUEUELENS_ADMIN_USERNAME / "
            "QUEUELENS_USERS_JSON): change it there",
        )


async def _audit_change(
    request: Request, actor: CurrentUser, action: str, metadata: dict[str, Any]
) -> None:
    """Account and configuration changes are audited like broker actions: who changed what."""
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    await request.app.state.audit_repository.record(AuditEntry(
        username=actor.username, action=action, timestamp=datetime.now(UTC),
        result="success", metadata=metadata,
    ))


@router.patch("/users/{username}")
async def update_user(
    request: Request,
    username: str,
    body: UserChange,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Change an account's role, or deactivate / reactivate it. Takes effect on its next
    request, on every replica. A deactivated account is refused through SSO as well."""
    _changeable(request, admin, username)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change: send role and/or active")
    if not await request.app.state.users.update(username, **changes):
        raise HTTPException(status_code=404, detail=f"No local account named {username}")
    # `role` on every audit row is the acting Admin's, so the new one is `new_role`
    audit = {"user": username, **{"new_role" if k == "role" else k: v for k, v in changes.items()}}
    await _audit_change(request, admin, "update_user", audit)
    return {"username": username, **changes}


@router.delete("/users/{username}")
async def delete_user(
    request: Request,
    username: str,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Remove a local account. Someone signing in through SSO still gets their group's
    role afterwards: deactivate them instead to keep them out."""
    _changeable(request, admin, username)
    if not await request.app.state.users.delete(username):
        raise HTTPException(status_code=404, detail=f"No local account named {username}")
    await _audit_change(request, admin, "delete_user", {"user": username})
    return {"deleted": username}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=10, max_length=255)


@router.post("/users/me/password")
async def change_my_password(
    request: Request,
    body: PasswordChange,
    user: CurrentUser = Depends(get_current_user_dep),
) -> dict[str, Any]:
    settings = request.app.state.settings
    if user.username in settings.users:
        raise HTTPException(
            status_code=400,
            detail=(
                "This account is managed by environment variables "
                "(QUEUELENS_ADMIN_PASSWORD / QUEUELENS_USERS_JSON) — change it there"
            ),
        )
    changed = await request.app.state.users.change_password(
        user.username, body.current_password, body.new_password
    )
    if not changed:
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    await _audit_change(request, user, "change_password", {"user": user.username})
    return {"changed": True}


# ---------------------------------------------------------------- environments


@router.get("/environments")
async def list_environments(
    request: Request,
    _username: str = Depends(get_current_username),
) -> dict[str, Any]:
    """`active` marks the environment this request is scoped to (its X-QueueLens-* headers)."""
    return {"environments": request.app.state.environment_manager.list(*requested_scope(request))}


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
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Create an environment or add vhosts to an existing one. Broker fields left
    blank inherit the default environment; credentials are stored encrypted when
    QUEUELENS_SECRET_KEY is set and are never echoed back."""
    username = user.username
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
        amqp_user = body.username or ""
        amqp_password = body.password or ""
        credentials = f"{amqp_user}:{amqp_password}@" if amqp_user else ""
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
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    username = user.username
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    try:
        await request.app.state.environment_manager.remove_custom(name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0])) from error
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
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    """Check that an environment/vhost is usable before a client switches to it. Nothing
    changes for anyone else: the client then sends X-QueueLens-Environment /
    X-QueueLens-Vhost with its requests."""
    username = user.username
    from datetime import UTC, datetime

    from app.domain.models import AuditEntry

    try:
        result = await request.app.state.environment_manager.activate(
            body.environment, body.vhost
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0])) from error
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
