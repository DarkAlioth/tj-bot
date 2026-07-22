from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from tj_bot.config import AppConfig


class ConfigMiddleware(BaseMiddleware):
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # signature dictated by aiogram BaseMiddleware
        data["config"] = self.config
        return await handler(event, data)
