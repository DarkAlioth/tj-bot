"""Routing of ``widget_query`` updates through aiogram 3.31, which predates them.

Delete together with :mod:`tj_bot.widgets.api` once aiogram dispatches the
update type itself.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Dispatcher, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.dispatcher.event.telegram import TelegramEventObserver
from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject, Update

from tj_bot.widgets.api import WidgetQuery

logger = logging.getLogger(__name__)

WIDGET_QUERY = "widget_query"
SHARED_WIDGET_QUERY = "shared_widget_query"


def widget_observer(router: Router) -> TelegramEventObserver:
    """The router's ``widget_query`` observer, created on first use.

    Living in ``router.observers`` makes ``propagate_event`` and
    ``resolve_used_update_types`` treat it like a built-in update type.
    """
    observer = router.observers.get(WIDGET_QUERY)
    if observer is None:
        observer = TelegramEventObserver(router=router, event_name=WIDGET_QUERY)
        router.observers[WIDGET_QUERY] = observer
    return observer


class WidgetRouter(Router):
    """A router with a ``widget_query`` observer.

    Usage: ``@router.widget_query(Verb("n", "p"))``.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self.widget_query = widget_observer(self)


class Verb(BaseFilter):
    """Match the request verb: ``data`` up to the first ``:``.

    Passes ``verb`` and the remaining ``args`` (``"g:17"`` -> ``["17"]``) to
    the handler.
    """

    def __init__(self, *verbs: str) -> None:
        self.verbs = frozenset(verbs)

    async def __call__(self, query: WidgetQuery) -> bool | dict[str, Any]:
        verb, *args = query.data.split(":")
        if verb not in self.verbs:
            return False
        return {"verb": verb, "args": args}


class WidgetQueryMiddleware(BaseMiddleware):
    """Outer middleware for ``dp.update`` that feeds ``widget_query`` observers.

    aiogram keeps the unknown update field in ``Update.model_extra``; left
    alone, ``_listen_update`` warns about an unknown update type and skips it.
    """

    def __init__(self, dispatcher: Dispatcher) -> None:
        self.dispatcher = dispatcher

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # signature dictated by aiogram BaseMiddleware
        extra = event.model_extra if isinstance(event, Update) else None
        if not extra:
            return await handler(event, data)
        if SHARED_WIDGET_QUERY in extra:
            # forwards of shared widgets are not supported; the type is not
            # in allowed_updates, so this only guards a misconfiguration
            logger.debug("Ignored a shared_widget_query update")
            return UNHANDLED
        raw = extra.get(WIDGET_QUERY)
        if not isinstance(raw, dict):
            return await handler(event, data)
        query = WidgetQuery.model_validate(raw, context={"bot": data["bot"]})
        return await self.dispatcher.propagate_event(
            update_type=WIDGET_QUERY, event=query, event_update=event, **data
        )


def widget_allowed_updates(dp: Dispatcher) -> list[str]:
    """``allowed_updates`` for ``start_polling``: used types plus ``widget_query``."""
    used = dp.resolve_used_update_types()
    return used if WIDGET_QUERY in used else [*used, WIDGET_QUERY]
