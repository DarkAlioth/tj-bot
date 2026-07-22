from types import SimpleNamespace
from typing import cast

from aiogram.types import TelegramObject

from tj_bot.config import AppConfig, JackettRuntime, Settings
from tj_bot.filters.admin import AdminOnly


def make_config(admin_ids: list[int]) -> AppConfig:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        admins=admin_ids,
        bot_token="42:TEST",
        postgres_user="u",
        postgres_password="p",  # noqa: S106
        postgres_db="d",
        db_host="db",
    )
    return AppConfig(
        settings=settings,
        jackett=JackettRuntime(url="http://jackett:9117", api_key="k"),
    )


def test_is_admin_matches_configured_ids() -> None:
    config = make_config([111, 222])
    assert config.is_admin(111) is True
    assert config.is_admin(222) is True
    assert config.is_admin(333) is False


def test_is_admin_rejects_none() -> None:
    assert make_config([111]).is_admin(None) is False


def test_is_admin_empty_admin_list() -> None:
    assert make_config([]).is_admin(111) is False


async def test_admin_only_filter_passes_admin() -> None:
    config = make_config([111])
    event = cast(TelegramObject, SimpleNamespace(from_user=SimpleNamespace(id=111)))
    assert await AdminOnly()(event, config) is True


async def test_admin_only_filter_blocks_non_admin() -> None:
    config = make_config([111])
    event = cast(TelegramObject, SimpleNamespace(from_user=SimpleNamespace(id=999)))
    assert await AdminOnly()(event, config) is False


async def test_admin_only_filter_blocks_missing_user() -> None:
    config = make_config([111])
    event = cast(TelegramObject, SimpleNamespace(from_user=None))
    assert await AdminOnly()(event, config) is False
