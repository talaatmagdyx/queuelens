import ipaddress
import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROLES = ("Viewer", "Operator", "Admin")  # lowest to highest


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUEUELENS_", case_sensitive=False, env_file=".env", extra="ignore"
    )

    app_name: str = "QueueLens"
    environment: str = "development"
    auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password: str = Field(default="change-me", repr=False)
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    rabbitmq_management_url: str = "http://rabbitmq:15672"
    rabbitmq_management_username: str = "guest"
    rabbitmq_management_password: str = Field(default="guest", repr=False)
    rabbitmq_vhost: str = "/"
    rabbitmq_connection_name: str = "queuelens"
    rabbitmq_operation_timeout_seconds: float = 10.0
    database_url: str = "sqlite+aiosqlite:///./data/queuelens.db"
    max_preview_messages: int = 100
    max_message_size_bytes: int = 1_048_576
    refetch_window_size: int = 100
    # how deep one browse snapshot reads (quorum queues deeper than this are refused)
    max_browse_depth: int = 5000
    replay_targets_json: str = "{}"
    max_bulk_size: int = 500
    bulk_dry_run_ttl_seconds: int = 600
    masking_enabled: bool = True
    masked_fields: str = (
        "password,token,access_token,refresh_token,authorization,api_key,x_api_key,secret,email,phone"
    )
    users_json: str = "{}"
    environments_json: str = "{}"
    smtp_host: str = ""  # seeds the email channel config (e.g. mailpit)
    smtp_port: int = 1025
    alert_interval_seconds: float = 15.0
    # Optional Fernet key (44-char urlsafe base64). When set, secret-bearing
    # settings (delivery channels, environment credentials) are encrypted at rest.
    secret_key: str = Field(default="", repr=False)
    # SSO behind an authenticating proxy (docs/SSO.md): the proxy names the signed-in user
    # in this header (X-Forwarded-User, Remote-User…), believed only from trusted_proxies
    auth_proxy_header: str = ""
    auth_proxy_groups_header: str = ""  # comma-separated groups (X-Forwarded-Groups…)
    auth_proxy_roles_json: str = "{}"  # {"group": "Admin" | "Operator" | "Viewer"}
    auth_proxy_default_role: str = "Viewer"  # users in no mapped group; "" refuses them
    # the proxies whose X-Forwarded-For / -Proto (and auth_proxy_header) are believed;
    # the image runs uvicorn with --no-proxy-headers, so this is the only place it's set
    trusted_proxies: str = "127.0.0.1,::1"

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_proxies(cls, value: str) -> str:
        for entry in _entries(value):
            ipaddress.ip_network(entry, strict=False)  # "*" and hostnames are refused
        return value

    @field_validator("auth_proxy_default_role")
    @classmethod
    def _valid_default_role(cls, value: str) -> str:
        if value and value not in ROLES:
            raise ValueError(f"must be one of {', '.join(ROLES)} or empty")
        return value

    @field_validator("auth_proxy_roles_json")
    @classmethod
    def _valid_roles(cls, value: str) -> str:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or not set(parsed.values()) <= set(ROLES):
            raise ValueError(f'must map group names to {", ".join(ROLES)}')
        return value

    @property
    def trusted_proxy_list(self) -> list[str]:
        return _entries(self.trusted_proxies)

    @property
    def auth_proxy_roles(self) -> dict[str, str]:
        return dict(json.loads(self.auth_proxy_roles_json))

    @property
    def users(self) -> dict[str, str]:
        """username -> password map; the admin account is always included."""
        try:
            parsed = json.loads(self.users_json)
        except json.JSONDecodeError as error:
            raise ValueError("QUEUELENS_USERS_JSON must contain valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("QUEUELENS_USERS_JSON must be an object")
        users = {str(name): str(password) for name, password in parsed.items()}
        users[self.admin_username] = self.admin_password
        return users

    @property
    def environments(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.environments_json)
        except json.JSONDecodeError as error:
            raise ValueError("QUEUELENS_ENVIRONMENTS_JSON must contain valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("QUEUELENS_ENVIRONMENTS_JSON must be an object")
        return parsed

    @property
    def masked_field_names(self) -> tuple[str, ...]:
        if not self.masking_enabled:
            return ()
        return tuple(field.strip() for field in self.masked_fields.split(",") if field.strip())

    @property
    def replay_targets(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.replay_targets_json)
        except json.JSONDecodeError as error:
            raise ValueError("QUEUELENS_REPLAY_TARGETS_JSON must contain valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("QUEUELENS_REPLAY_TARGETS_JSON must be an object")
        return parsed


def _entries(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
