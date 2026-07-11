"""Multi-environment support: per-(environment, vhost) broker bundles with live switching."""

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
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.infrastructure.rabbitmq.message_operator import MessageOperator

logger = logging.getLogger(__name__)


@dataclass
class Bundle:
    settings: Settings
    connection: RabbitMQConnection
    management: RabbitMQManagementClient
    message_service: MessageService
    action_service: ActionService
    bulk_service: BulkActionService
    queue_service: QueueService
    started: bool = False


def _build_bundle(settings: Settings) -> Bundle:
    connection = RabbitMQConnection(settings)
    browser = MessageBrowser(connection)
    operator = MessageOperator(connection)
    management = RabbitMQManagementClient(settings)
    return Bundle(
        settings=settings,
        connection=connection,
        management=management,
        message_service=MessageService(browser),
        action_service=ActionService(settings, operator),
        bulk_service=BulkActionService(settings, browser, operator),
        queue_service=QueueService(management),
    )


def _amqp_url_for_vhost(url: str, vhost: str) -> str:
    from urllib.parse import quote

    base = url.rsplit("/", 1)[0] if url.count("/") > 2 else url.rstrip("/")
    # the default vhost "/" is an empty path; any other name is percent-encoded
    suffix = "" if vhost == "/" else quote(vhost, safe="")
    return f"{base}/{suffix}"


class EnvironmentManager:
    """Owns one service bundle per (environment, vhost) and swaps app.state on activate."""

    def __init__(self, app_state: Any, base_settings: Settings) -> None:
        self._state = app_state
        self._base = base_settings
        self._bundles: dict[tuple[str, str], Bundle] = {}
        self.active_env = base_settings.environment
        self.active_vhost = base_settings.rabbitmq_vhost
        self._profiles = self._load_profiles(base_settings)

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

    def apply_custom(self, stored: dict[str, Any]) -> None:
        """Merge server-stored environments: new names inherit the default broker;
        existing names gain any extra vhosts. Idempotent."""
        default = self._profiles[self._base.environment]
        for name, custom in (stored or {}).items():
            vhosts = [str(v) for v in (custom or {}).get("vhosts", []) if str(v).strip()]
            if name in self._profiles:
                for vhost in vhosts:
                    if vhost not in self._profiles[name]["vhosts"]:
                        self._profiles[name]["vhosts"].append(vhost)
            else:
                profile = dict(default)
                profile["vhosts"] = vhosts or ["/"]
                self._profiles[name] = profile

    def list(self) -> list[dict[str, Any]]:
        out = []
        for name, profile in self._profiles.items():
            out.append(
                {
                    "id": name,
                    "api": profile["management_url"],
                    "vhosts": profile["vhosts"],
                    "active": name == self.active_env,
                    "active_vhost": self.active_vhost if name == self.active_env else None,
                }
            )
        return out

    def attach_default(self) -> Bundle:
        """Build the default bundle and expose it on app.state without starting it."""
        key = (self.active_env, self.active_vhost)
        bundle = self._bundles.get(key)
        if bundle is None:
            bundle = _build_bundle(self._settings_for(self.active_env, self.active_vhost))
            self._bundles[key] = bundle
        self._swap(bundle)
        return bundle

    async def start_default(self) -> Bundle:
        bundle = await self._ensure_bundle(self.active_env, self.active_vhost)
        self._swap(bundle)
        return bundle

    async def _ensure_bundle(self, env: str, vhost: str) -> Bundle:
        key = (env, vhost)
        bundle = self._bundles.get(key)
        if bundle is None:
            bundle = _build_bundle(self._settings_for(env, vhost))
            self._bundles[key] = bundle
        if not bundle.started:
            await bundle.management.start()
            if vhost != "/":
                # vhosts named in a profile are created on first use (idempotent)
                try:
                    await bundle.management.ensure_vhost(vhost)
                except Exception:  # noqa: BLE001 - surfaced via connection failure below
                    logger.exception("could not ensure vhost %s", vhost)
            await bundle.connection.start()
            bundle.connection.start_reconnect_loop()
            bundle.started = True
        return bundle

    def _swap(self, bundle: Bundle) -> None:
        self._state.settings = bundle.settings  # /api/broker and /api/config report the active env
        self._state.rabbitmq_connection = bundle.connection
        self._state.management_client = bundle.management
        self._state.message_service = bundle.message_service
        self._state.action_service = bundle.action_service
        self._state.bulk_service = bundle.bulk_service
        self._state.queue_service = bundle.queue_service

    async def activate(self, env: str, vhost: str | None = None) -> dict[str, Any]:
        if env not in self._profiles:
            raise KeyError(f"Unknown environment: {env}")
        profile = self._profiles[env]
        vhost = vhost or str(profile["vhosts"][0])
        if vhost not in profile["vhosts"]:
            profile["vhosts"].append(vhost)
        bundle = await self._ensure_bundle(env, vhost)
        if not bundle.connection.is_connected:
            raise ConnectionError(
                f"Could not connect to {env} (vhost {vhost}) — check the profile and permissions"
            )
        self._swap(bundle)
        self.active_env = env
        self.active_vhost = vhost
        return {"environment": env, "vhost": vhost}

    async def stop_all(self) -> None:
        for bundle in self._bundles.values():
            if bundle.started:
                await bundle.connection.close()
                await bundle.management.close()
                bundle.started = False
