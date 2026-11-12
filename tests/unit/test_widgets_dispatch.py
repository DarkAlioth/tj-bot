import warnings
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Message, TelegramObject, Update

from tj_bot.widgets.api import AnswerWidgetQuery, WidgetQuery
from tj_bot.widgets.dispatch import (
    Verb,
    WidgetQueryMiddleware,
    WidgetRouter,
    widget_allowed_updates,
    widget_observer,
)

USER = {"id": 111111111, "is_bot": False, "first_name": "Alice"}
CHAT = {"id": 111111111, "type": "private", "first_name": "Alice"}


def widget_update(data: str, field: str = "widget_query") -> Update:
    query: dict[str, Any] = {"id": "q1", "from": USER, "data": data}
    if field == "widget_query":
        query |= {"chat": CHAT, "message_id": 4242, "chat_instance": "ci"}
    else:
        query["widget_id"] = "w"
    return Update.model_validate({"update_id": 1, field: query})


def message_update() -> Update:
    return Update.model_validate(
        {
            "update_id": 2,
            "message": {"message_id": 1, "date": 0, "chat": CHAT, "text": "hi"},
        }
    )


def build() -> tuple[Dispatcher, WidgetRouter, list[dict[str, Any]]]:
    dp = Dispatcher()
    dp["jackett"] = "jackett-client"
    dp.update.outer_middleware(WidgetQueryMiddleware(dp))
    router = WidgetRouter()
    calls: list[dict[str, Any]] = []

    @router.widget_query(Verb("g", "n"))
    async def go(
        query: WidgetQuery, verb: str, args: list[str], bot: Bot, jackett: str
    ) -> AnswerWidgetQuery:
        calls.append({"verb": verb, "args": args, "bot": bot, "jackett": jackett})
        return query.answer(verb)

    dp.include_router(router)
    return dp, router, calls


async def test_widget_query_routed_to_verb_handler_with_di() -> None:
    dp, _, calls = build()
    bot = Bot(token="42:TEST")

    # the update is built without a bot context on purpose: feed_update then
    # re-validates it from model_dump(), which must keep the extra field
    result = await dp.feed_update(bot, widget_update("g:17"))

    assert isinstance(result, AnswerWidgetQuery)
    assert result.widget_query_id == "q1"
    assert result.data == "g"
    assert result.bot is bot
    assert calls == [
        {"verb": "g", "args": ["17"], "bot": bot, "jackett": "jackett-client"}
    ]


async def test_unmatched_verb_is_unhandled() -> None:
    dp, _, calls = build()

    result = await dp.feed_update(Bot(token="42:TEST"), widget_update("zzz"))

    assert result is UNHANDLED
    assert calls == []


async def test_global_outer_middleware_on_dispatcher_observer_runs() -> None:
    dp, router, _ = build()
    seen: list[TelegramObject] = []

    async def mark(
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # aiogram middleware signature
        seen.append(event)
        data["marker"] = "m"
        return await handler(event, data)

    widget_observer(dp).outer_middleware(mark)
    got: list[str] = []

    @router.widget_query(Verb("x"))
    async def take(query: WidgetQuery, marker: str) -> None:
        got.append(marker)

    await dp.feed_update(Bot(token="42:TEST"), widget_update("x"))

    assert len(seen) == 1 and isinstance(seen[0], WidgetQuery)
    assert got == ["m"]


async def test_shared_widget_query_ignored_without_warning() -> None:
    dp, _, calls = build()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await dp.feed_update(
            Bot(token="42:TEST"), widget_update("x", field="shared_widget_query")
        )

    assert result is UNHANDLED
    assert calls == []
    assert caught == []


async def test_known_update_types_pass_through_untouched() -> None:
    dp, _, calls = build()
    texts: list[str | None] = []

    @dp.message()
    async def on_message(message: Message) -> str:
        texts.append(message.text)
        return "handled"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await dp.feed_update(Bot(token="42:TEST"), message_update())

    assert result == "handled"
    assert texts == ["hi"]
    assert calls == []
    assert caught == []


async def test_other_unknown_update_types_still_reach_aiogram() -> None:
    dp, _, calls = build()
    update = Update.model_validate({"update_id": 3, "future_update": {"id": 1}})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await dp.feed_update(Bot(token="42:TEST"), update)

    assert result is UNHANDLED
    assert calls == []
    assert [type(w.message) for w in caught] == [RuntimeWarning]


async def test_verb_filter_matches_and_splits_args() -> None:
    query = WidgetQuery.model_validate(
        {"id": "q", "from": USER, "chat_instance": "ci", "data": "del:1:x"}
    )

    assert await Verb("del", "x")(query) == {"verb": "del", "args": ["1", "x"]}
    assert await Verb("x")(query) is False


def test_widget_allowed_updates_contains_widget_query_once() -> None:
    dp, _, _ = build()

    @dp.message()
    async def on_message(message: Message) -> None:
        pass

    assert widget_allowed_updates(dp) == ["message", "widget_query"]
    assert widget_allowed_updates(Dispatcher()) == ["widget_query"]


def test_widget_observer_is_created_once_per_router() -> None:
    router = Router()

    observer = widget_observer(router)

    assert widget_observer(router) is observer
    assert router.observers["widget_query"] is observer
