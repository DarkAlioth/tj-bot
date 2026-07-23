import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from tj_bot.config import AppConfig

logger = logging.getLogger(__name__)

MAX_TRACKED_USERS = 5000
# Search commands and the cache-bypassing refresh button both hit trackers.
COSTLY_TEXT_PREFIXES = ("/s ", "/s")
COSTLY_CALLBACK_PREFIXES = ("upd:",)


class ThrottlingMiddleware(BaseMiddleware):
    """Per-user rate limits for the open bot: pacing + a quota on costly actions.

    Applies to both messages and callback queries. Admins are exempt. State is
    in-memory and evicted LRU-style once it grows past the cap.
    """

    def __init__(
        self,
        min_interval: float = 0.7,
        costly_limit: int = 5,
        costly_window: float = 60.0,
        notice_interval: float = 5.0,
    ) -> None:
        self.min_interval = min_interval
        self.costly_limit = costly_limit
        self.costly_window = costly_window
        self.notice_interval = notice_interval
        self._last_seen: OrderedDict[int, float] = OrderedDict()
        self._costly: dict[int, deque[float]] = {}
        self._last_notice: dict[int, float] = {}

    def _touch(self, user_id: int, now: float) -> None:
        self._last_seen[user_id] = now
        self._last_seen.move_to_end(user_id)
        while len(self._last_seen) > MAX_TRACKED_USERS:
            evicted, _ = self._last_seen.popitem(last=False)
            self._costly.pop(evicted, None)
            self._last_notice.pop(evicted, None)

    @staticmethod
    def _is_costly(event: Message | CallbackQuery) -> bool:
        if isinstance(event, Message):
            text = event.text or ""
            # /last replays a search and hits trackers when the cache expired
            return text in ("/s", "/last") or text.startswith("/s ")
        return (event.data or "").startswith(COSTLY_CALLBACK_PREFIXES)

    @staticmethod
    async def _silence(event: Message | CallbackQuery) -> None:
        """Ack a dropped callback so the client does not spin until timeout."""
        if not isinstance(event, CallbackQuery):
            return
        try:
            await event.answer()
        except TelegramAPIError:
            logger.debug("Failed to ack a throttled callback", exc_info=True)

    async def _notify(
        self, event: Message | CallbackQuery, user_id: int, now: float
    ) -> None:
        if now - self._last_notice.get(user_id, 0.0) <= self.notice_interval:
            await self._silence(event)
            return
        self._last_notice[user_id] = now
        notice = "⏳ Слишком много запросов, подождите минуту."
        if isinstance(event, CallbackQuery):
            await event.answer(notice, show_alert=True)
        else:
            await event.answer(notice)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # signature dictated by aiogram BaseMiddleware
        if not isinstance(event, Message | CallbackQuery) or event.from_user is None:
            return await handler(event, data)
        config = data.get("config")
        user_id = event.from_user.id
        if isinstance(config, AppConfig) and config.is_admin(user_id):
            return await handler(event, data)

        now = time.monotonic()
        last = self._last_seen.get(user_id)
        self._touch(user_id, now)
        if last is not None and now - last < self.min_interval:
            # drop machine-speed repeats, but still ack callbacks: an
            # unanswered callback leaves the button spinner running
            await self._silence(event)
            return None

        if self._is_costly(event):
            timestamps = self._costly.setdefault(user_id, deque())
            while timestamps and now - timestamps[0] > self.costly_window:
                timestamps.popleft()
            if len(timestamps) >= self.costly_limit:
                await self._notify(event, user_id, now)
                return None
            timestamps.append(now)
        return await handler(event, data)
