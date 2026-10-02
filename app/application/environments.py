"""Multi-environment support: one broker bundle per (environment, vhost), chosen per request.

There is no instance-wide "active" environment: every request names the environment and
vhost it means (X-QueueLens-Environment / X-QueueLens-Vhost, see app.api.scope), so two
operators — or two tabs — can work against different brokers at once without one
re-pointing the other's views and actions."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.application.action_service import ActionService
from app.application.bulk_service import BulkActionService
from app.application.message_service import MessageService
from app.application.queue_service import QueueService
from app.config import Settings
from app.infrastructure.rabbitmq.connection import RabbitMQConnection
from app.infrastructure.rabbitmq.management_client import RabbitMQManagementClient
from app.infrastructure.rabbitmq.message_browser import MessageBrowser, QueueLocks
from app.infrastructure.rabbitmq.message_operator import MessageOperator

logger = logging.getLogger(__name__)


@dataclass
class Bundle:
    """Field names match app.state, which holds the default environment's bundle — routes
    read either through app.api.scope.broker()."""

    settings: Settings
    rabbitmq_connection: RabbitMQConnection
    management_client: RabbitMQManagementClient
    message_service: MessageService
    action_service: ActionService
    bulk_service: BulkActionService
    queue_service: QueueService
    started: bool = False
    topology_cache: Any = None


def _build_bundle(settings: Settings, batch_store: Any = None) -> Bundle:
    connection = RabbitMQConnection(settings)
    locks = QueueLocks()  # previews and actions on one queue never interleave
    browser = MessageBrowser(connection, locks)
    operator = MessageOperator(connection, locks)
    management = RabbitMQManagementClient(settings)
    queue_service = QueueService(management)
    guard = queue_service.assert_browsable
    return Bundle(
        settings=settings,
        rabbitmq_connection=connection,
        management_client=management,
        message_service=MessageService(browser, guard),
        action_service=ActionService(settings, operator, guard),
        bulk_service=BulkActionService(settings, browser, operator, batch_store, guard),
        queue_service=queue_service,
    )


def _amqp_url_for_vhost(url: str, vhost: str) -> str:
    from urllib.parse import quote

    base = url.rsplit("/", 1)[0] if url.count("/") > 2 else url.rstrip("/")
    # the default vhost "/" is an empty path; any other name is percent-encoded
    suffix = "" if vhost == "/" else quote(vhost, safe="")
    return f"{base}/{suffix}"


class EnvironmentManager:
    """Owns one service bundle per (environment, vhost); the default one is exposed on
    app.state, the others are resolved per request."""

    def __init__(self, app_state: Any, base_settings: Settings, batch_store: Any = None) -> None:
        self._batch_store = batch_store
        self._state = app_state
        self._base = base_settings
        self._bundles: dict[tuple[str, str], Bundle] = {}
        self.default_key = (base_settings.environment, base_settings.rabbitmq_vhost)
        self._profiles = self._load_profiles(base_settings)
        self._custom: set[str] = set()
        self._starting = asyncio.Lock()  # concurrent first requests start a bundle once

    @staticmethod
    def _load_profiles(settings: Settings) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {
            settings.environment: {
                "rabbitmq_url": settings.rabbitmq_url,
                "management_url": settings.rabbitmq_management_url,
                "management_username": settings.rabbitmq_management_username,
                "management_password": settings.rabbitmq_management_password,
                "vhosts": [settings.rabbitmq_vhost],
            }
        }
        for name, profile in settings.environments.items():
            merged = dict(profiles[settings.environment])
            merged.update(profile)
            merged.setdefault("vhosts", ["/"])
            profiles[name] = merged
        return profiles

    def _settings_for(self, env: str, vhost: str) -> Settings:
        profile = self._profiles[env]
        return self._base.model_copy(
            update={
                "environment": env,
                "rabbitmq_url": _amqp_url_for_vhost(profile["rabbitmq_url"], vhost),
                "rabbitmq_management_url": profile["management_url"],
                "rabbitmq_management_username": profile["management_username"],
                "rabbitmq_management_password": profile["management_password"],
                "rabbitmq_vhost": vhost,
            }
        )

    CUSTOM_FIELDS = (
        "rabbitmq_url",
        "management_url",
        "management_username",
        "management_password",
    )

    def apply_custom(self, stored: dict[str, Any]) -> None:
        """Merge server-stored environments. Omitted broker fields inherit the
        default environment; existing names gain extra vhosts and any field
        overrides. Idempotent."""
        default = self._profiles[self._base.environment]
        for name, custom in (stored or {}).items():
            custom = custom or {}
            vhosts = [str(v) for v in custom.get("vhosts", []) if str(v).strip()]
            overrides = {k: custom[k] for k in self.CUSTOM_FIELDS if custom.get(k)}
            if name in self._profiles:
                self._profiles[name].update(overrides)
                for vhost in vhosts:
                    if vhost not in self._profiles[name]["vhosts"]:
                        self._profiles[name]["vhosts"].append(vhost)
            else:
                profile = dict(default)
                profile.update(overrides)
                profile["vhosts"] = vhosts or ["/"]
                self._profiles[name] = profile
            if name != self._base.environment:
                self._custom.add(name)

    async def remove_custom(self, name: str) -> None:
        """Drop a server-added environment (never the default or env-var ones) and close
        its connections; requests still naming it get 404 from then on."""
        if name not in self._custom:
            raise KeyError(f"{name} is not a removable environment")
        self._custom.discard(name)
        self._profiles.pop(name, None)
        for key in [k for k in self._bundles if k[0] == name]:
            await self._stop(self._bundles.pop(key))

    def scope(self, env: str | None, vhost: str | None) -> tuple[str, str]:
        """Validate a requested (environment, vhost); blanks mean the default environment
        and that environment's first vhost."""
        env = env or self.default_key[0]
        if env not in self._profiles:
            raise KeyError(f"Unknown environment: {env}")
        vhosts = self._profiles[env]["vhosts"]
        if not vhost:
            vhost = self.default_key[1] if env == self.default_key[0] else str(vhosts[0])
        if vhost not in vhosts:
            # using a vhost creates it on the broker — only Admins may add one
            # (POST /api/environments), so an Operator can't mint vhosts by naming one
            raise KeyError(f"Unknown vhost {vhost!r} for environment {env} — add it first")
        return env, vhost

    async def resolve(self, env: str | None, vhost: str | None) -> Any:
        """The services for one request's scope: app.state for the default (where tests
        override them), otherwise that scope's bundle, started on first use."""
        key = self.scope(env, vhost)
        if key == self.default_key:
            return self._state
        bundle = self._bundles.get(key)
        if bundle is not None and bundle.started:
            return bundle
        # ponytail: one lock for all first starts — per-key locks if slow brokers queue up
        async with self._starting:
            return await self._ensure_bundle(*key)

    def list(self, env: str | None = None, vhost: str | None = None) -> list[dict[str, Any]]:
        """Every environment; `active` marks the one the asking request is scoped to."""
        try:
            current = self.scope(env, vhost)
        except KeyError:
            current = self.default_key
        out = []
        for name, profile in self._profiles.items():
            out.append(
                {
                    "id": name,
                    "api": profile["management_url"],
                    "vhosts": profile["vhosts"],
                    "default": name == self.default_key[0],
                    "active": name == current[0],
                    "active_vhost": current[1] if name == current[0] else None,
                    "removable": name in self._custom,
                }
            )
        return out

    def attach_default(self) -> Bundle:
        """Build the default bundle and expose it on app.state without starting it."""
        bundle = self._bundles.get(self.default_key)
        if bundle is None:
            bundle = _build_bundle(self._settings_for(*self.default_key), self._batch_store)
            self._bundles[self.default_key] = bundle
        for name in (
            "settings", "rabbitmq_connection", "management_client", "message_service",
            "action_service", "bulk_service", "queue_service", "topology_cache",
        ):
            setattr(self._state, name, getattr(bundle, name))
        return bundle

    async def start_default(self) -> Bundle:
        bundle = await self._ensure_bundle(*self.default_key)
        self.attach_default()
        return bundle

    async def _ensure_bundle(self, env: str, vhost: str) -> Bundle:
        key = (env, vhost)
        bundle = self._bundles.get(key)
        if bundle is None:
            bundle = _build_bundle(self._settings_for(env, vhost), self._batch_store)
            self._bundles[key] = bundle
        if not bundle.started:
            await bundle.management_client.start()
            if vhost != "/":
                # vhosts named in a profile are created on first use (idempotent)
                try:
                    await bundle.management_client.ensure_vhost(vhost)
                except Exception:  # noqa: BLE001 - surfaced via connection failure below
                    logger.exception("could not ensure vhost %s", vhost)
            await bundle.rabbitmq_connection.start()
            bundle.rabbitmq_connection.start_reconnect_loop()
            bundle.started = True
        return bundle

    async def activate(self, env: str, vhost: str | None = None) -> dict[str, Any]:
        """Check that a scope is usable before a client switches to it. Changes nothing
        server-side — the client sends the scope with its requests from then on."""
        key = self.scope(env, vhost)
        services = await self.resolve(*key)
        if not services.rabbitmq_connection.is_connected:
            raise ConnectionError(
                f"Could not connect to {key[0]} (vhost {key[1]}) — check the profile and "
                "permissions"
            )
        return {"environment": key[0], "vhost": key[1]}

    @staticmethod
    async def _stop(bundle: Bundle) -> None:
        if bundle.started:
            await bundle.rabbitmq_connection.close()
            await bundle.management_client.close()
            bundle.started = False

    async def stop_all(self) -> None:
        for bundle in self._bundles.values():
            await self._stop(bundle)
