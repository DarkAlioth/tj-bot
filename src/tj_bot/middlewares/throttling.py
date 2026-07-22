import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from tj_bot.config import AppConfig

MAX_TRACKED_USERS = 5000


class ThrottlingMiddleware(BaseMiddleware):
    """Per-user rate limits for the open bot: pacing + search quota.

    Admins are exempt. State is in-memory and bounded.
    """

    def __init__(
        self,
        min_interval: float = 0.7,
        search_limit: int = 5,
        search_window: float = 60.0,
        notice_interval: float = 5.0,
    ) -> None:
        self.min_interval = min_interval
        self.search_limit = search_limit
        self.search_window = search_window
        self.notice_interval = notice_interval
        self._last_message: dict[int, float] = {}
        self._searches: dict[int, deque[float]] = {}
        self._last_notice: dict[int, float] = {}

    def _evict_if_needed(self) -> None:
        if len(self._last_message) > MAX_TRACKED_USERS:
            self._last_message.clear()
            self._searches.clear()
            self._last_notice.clear()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # signature dictated by aiogram BaseMiddleware
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)
        config = data.get("config")
        user_id = event.from_user.id
        if isinstance(config, AppConfig) and config.is_admin(user_id):
            return await handler(event, data)

        self._evict_if_needed()
        now = time.monotonic()
        if (
            now - self._last_message.get(user_id, -self.min_interval)
            < self.min_interval
        ):
            return None  # silently drop machine-speed repeats
        self._last_message[user_id] = now

        text = event.text or ""
        if text == "/s" or text.startswith("/s "):
            timestamps = self._searches.setdefault(user_id, deque())
            while timestamps and now - timestamps[0] > self.search_window:
                timestamps.popleft()
            if len(timestamps) >= self.search_limit:
                if now - self._last_notice.get(user_id, 0.0) > self.notice_interval:
                    self._last_notice[user_id] = now
                    await event.answer("⏳ Слишком много запросов, подождите минуту.")
                return None
            timestamps.append(now)
        return await handler(event, data)
