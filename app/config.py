import json
from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUEUELENS_", case_sensitive=False)

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
    replay_targets_json: str = "{}"

    @property
    def replay_targets(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.replay_targets_json)
        except json.JSONDecodeError as error:
            raise ValueError("QUEUELENS_REPLAY_TARGETS_JSON must contain valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("QUEUELENS_REPLAY_TARGETS_JSON must be an object")
        return parsed


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
