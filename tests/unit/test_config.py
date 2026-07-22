import json
from pathlib import Path
from typing import Any

import pytest

from tj_bot.config import AppConfig, JackettRuntime, Settings, wait_for_jackett_api_key


def make_settings(**overrides: Any) -> Settings:  # noqa: ANN401  # test helper
    values: dict[str, Any] = {
        "bot_token": "42:TEST",
        "admins": "111",
        "postgres_user": "u",
        "postgres_password": "p",
        "postgres_db": "d",
        "db_host": "db",
    }
    values.update(overrides)
    # type ignore: kwargs are validated by pydantic at runtime; _env_file is a
    # settings-source argument invisible to the generated __init__ signature
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_admins_parsed_from_comma_separated_string() -> None:
    settings = make_settings(admins="111, 222,333")
    assert settings.admins == [111, 222, 333]


def test_admins_empty_by_default() -> None:
    settings = make_settings(admins="")
    assert settings.admins == []


def test_db_port_defaults_to_5432() -> None:
    assert make_settings().db_port == 5432


def test_app_config_exposes_admin_ids() -> None:
    config = AppConfig(
        settings=make_settings(admins="7"),
        jackett=JackettRuntime(url="http://jackett:9117", api_key="k"),
    )
    assert config.admin_ids == [7]


async def test_jackett_api_key_read_from_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "ServerConfig.json"
    config_file.write_text(json.dumps({"APIKey": "secret"}), encoding="utf-8")
    settings = make_settings(jackett_config_path=config_file)

    assert await wait_for_jackett_api_key(settings, attempts=1, delay=0) == "secret"


async def test_jackett_api_key_missing_file_raises(tmp_path: Path) -> None:
    settings = make_settings(jackett_config_path=tmp_path / "absent.json")

    with pytest.raises(RuntimeError, match="Jackett API key not available"):
        await wait_for_jackett_api_key(settings, attempts=2, delay=0)
