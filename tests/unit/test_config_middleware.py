from typing import Any, cast
from unittest.mock import AsyncMock

from aiogram.types import TelegramObject

from tj_bot.config import AppConfig
from tj_bot.middlewares.config import ConfigMiddleware


async def test_config_injected_into_handler_data() -> None:
    config = cast(AppConfig, object())
    middleware = ConfigMiddleware(config)
    handler = AsyncMock(return_value="handled")
    event = cast(TelegramObject, object())
    data: dict[str, Any] = {}

    result = await middleware(handler, event, data)

    assert result == "handled"
    assert data["config"] is config
    handler.assert_awaited_once_with(event, data)
