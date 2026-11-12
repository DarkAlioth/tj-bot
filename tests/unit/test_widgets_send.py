from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from tj_bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    SendWidget,
    WidgetQuery,
)
from tj_bot.widgets.send import (
    AnswerTooLargeError,
    answer_error,
    answer_json,
    dumps_answer,
    edit_widget_state,
    send_widget,
)

QUERY = WidgetQuery.model_validate(
    {
        "id": "q1",
        "from": {"id": 1, "is_bot": False, "first_name": "A"},
        "chat": {"id": 1, "type": "private"},
        "message_id": 7,
        "chat_instance": "ci",
        "data": "n:x",
    }
)


def test_dumps_answer_is_compact_and_keeps_cyrillic() -> None:
    assert dumps_answer({"ok": 1, "m": "Готово"}) == '{"ok":1,"m":"Готово"}'


def test_dumps_answer_rejects_oversized_payload() -> None:
    with pytest.raises(AnswerTooLargeError):
        dumps_answer({"pl": "ж" * 600})


def test_answer_json_and_error_build_the_method() -> None:
    method = answer_json(QUERY, {"ok": 1})
    assert isinstance(method, AnswerWidgetQuery)
    assert method.widget_query_id == "q1"
    assert method.data == '{"ok":1}'
    assert answer_error(QUERY, "Нет").data == '{"err":"Нет"}'


def fake_bot(result: object) -> AsyncMock:
    bot = AsyncMock()
    bot.return_value = result
    return bot


def sent_method(bot: AsyncMock) -> object:
    assert bot.await_args is not None
    return bot.await_args.args[0]


async def test_send_widget_sends_html_state_caption_and_records_context() -> None:
    message = MagicMock(spec=Message)
    message.message_id = 42
    bot = fake_bot(message)
    repo = AsyncMock()
    sent = await send_widget(
        bot,
        1,
        "search",
        {"v": 1, "r": 3},
        repo=repo,
        kind="search",
        context={"qh": "abcd"},
    )
    assert sent is message
    method = sent_method(bot)
    assert isinstance(method, SendWidget)
    assert method.chat_id == 1
    assert method.html.startswith("<")
    assert method.chat_data == '{"v":1,"r":3}'
    assert method.caption is None
    assert method.allow_queries is True
    repo.save_widget_message.assert_awaited_once_with(
        1, 42, "search", {"qh": "abcd"}, rev=3
    )


async def test_edit_widget_state_updates_the_record() -> None:
    bot = fake_bot(True)
    repo = AsyncMock()
    await edit_widget_state(bot, 1, 42, {"v": 1, "r": 4}, repo=repo, context={"i": 1})
    method = sent_method(bot)
    assert isinstance(method, EditMessageWidgetData)
    assert method.chat_data == '{"v":1,"r":4}'
    repo.update_widget_message.assert_awaited_once_with(1, 42, context={"i": 1}, rev=4)


async def test_edit_widget_state_treats_not_modified_as_done() -> None:
    bot = AsyncMock(
        side_effect=TelegramBadRequest(
            method=EditMessageWidgetData(chat_id=1, message_id=42, chat_data="{}"),
            message="Bad Request: message is not modified",
        )
    )
    await edit_widget_state(bot, 1, 42, {"v": 1, "r": 4})


async def test_edit_widget_state_raises_other_errors() -> None:
    bot = AsyncMock(
        side_effect=TelegramBadRequest(
            method=EditMessageWidgetData(chat_id=1, message_id=42, chat_data="{}"),
            message="Bad Request: message to edit not found",
        )
    )
    with pytest.raises(TelegramBadRequest):
        await edit_widget_state(bot, 1, 42, {"v": 1, "r": 4})
