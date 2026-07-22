import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    admins: Annotated[list[int], NoDecode] = []

    postgres_user: str
    postgres_password: str
    postgres_db: str
    db_host: str
    db_port: int = 5432

    jackett_url: str = "http://jackett:9117"
    jackett_config_path: Path = Path("/config/ServerConfig.json")
    jackett_timeout_seconds: float = 90
    download_max_bytes: int = 10 * 1024 * 1024

    cache_ttl_days: int = 7
    cleanup_interval_seconds: int = 3600
    search_cache_seconds: int = 3600
    subscriptions_per_user: int = 3
    subscriptions_check_seconds: int = 6 * 3600

    # Send-to-server via qBittorrent (admin only). Disabled unless all three set.
    qbit_url: str | None = None
    qbit_username: str | None = None
    qbit_password: str | None = None
    qbit_category: str | None = "tj-bot"
    qbit_poll_interval_seconds: int = 30
    qbit_watch_timeout_seconds: int = 24 * 60 * 60

    @property
    def qbit_enabled(self) -> bool:
        return bool(self.qbit_url and self.qbit_username and self.qbit_password)

    @field_validator("admins", mode="before")
    @classmethod
    def _parse_admins(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value


def load_settings() -> Settings:
    # type ignore: field values are sourced from the environment / .env file,
    # which mypy cannot see through the dataclass_transform-generated __init__
    return Settings()  # type: ignore[call-arg]


@dataclass(frozen=True)
class JackettRuntime:
    url: str
    api_key: str


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    jackett: JackettRuntime

    @property
    def admin_ids(self) -> list[int]:
        return self.settings.admins

    @property
    def qbit_enabled(self) -> bool:
        return self.settings.qbit_enabled

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.settings.admins


async def wait_for_jackett_api_key(
    settings: Settings, attempts: int = 30, delay: float = 2.0
) -> str:
    """Read the Jackett API key from its config volume.

    Jackett creates ServerConfig.json on its first start, so the file may not
    exist yet when the bot boots — poll until it appears.
    """
    for attempt in range(attempts):
        try:
            raw = settings.jackett_config_path.read_text(encoding="utf-8")
            api_key = json.loads(raw).get("APIKey")
        except (OSError, json.JSONDecodeError):
            api_key = None
        if api_key:
            return str(api_key)
        if attempt < attempts - 1:
            await asyncio.sleep(delay)
    msg = f"Jackett API key not available at {settings.jackett_config_path}"
    raise RuntimeError(msg)
