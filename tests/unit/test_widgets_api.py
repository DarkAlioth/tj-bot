import json
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tj_bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    EditSharedWidgetData,
    SendSharedWidget,
    SendWidget,
    SharedWidgetQuery,
    Widget,
    WidgetQuery,
    WidgetQueryTypeData,
    WidgetQueryTypeSentTransaction,
)

# the example from the widgets guide, "Requests to the bot"
DOCS_QUERY: dict[str, Any] = {
    "id": "8123456789012345678",
    "from": {
        "id": 111111111,
        "is_bot": False,
        "first_name": "Alice",
        "language_code": "en",
    },
    "chat": {"id": 111111111, "type": "private", "first_name": "Alice"},
    "message_id": 4242,
    "chat_instance": "901234567890123456",
    "widget_id": "7770011223344556677",
    "data": "refresh",
}

MARKUP = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="c")]]
)


def test_send_widget_api_method_and_payload() -> None:
    method = SendWidget(
        chat_id=1,
        html="<b>hi</b>",
        chat_data='{"v":1}',
        caption="cap",
        allow_queries=True,
        reply_markup=MARKUP,
    )

    assert SendWidget.__api_method__ == "sendWidget"
    assert SendWidget.__returning__ is Message
    payload = method.model_dump(exclude_none=True)
    assert payload["chat_id"] == 1
    assert payload["html"] == "<b>hi</b>"
    assert payload["chat_data"] == '{"v":1}'
    assert payload["caption"] == "cap"
    assert payload["allow_queries"] is True
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "x", "callback_data": "c"}]]
    }
    assert "width" not in payload


def test_send_widget_wire_values_resolve_bot_defaults() -> None:
    bot = Bot(token="42:TEST", default=DefaultBotProperties(parse_mode="HTML"))
    method = SendWidget(chat_id=1, html="<b/>", caption="c", reply_markup=MARKUP)
    files: dict[str, Any] = {}

    wire = {
        key: bot.session.prepare_value(value, bot=bot, files=files)
        for key, value in method.model_dump(warnings=False).items()
    }

    assert wire["parse_mode"] == "HTML"
    assert wire["protect_content"] is None
    assert json.loads(wire["reply_markup"]) == {
        "inline_keyboard": [[{"text": "x", "callback_data": "c"}]]
    }


def test_other_methods_names_and_returns() -> None:
    assert SendSharedWidget.__api_method__ == "sendSharedWidget"
    assert SendSharedWidget.__returning__ is Message
    assert EditMessageWidgetData.__api_method__ == "editMessageWidgetData"
    assert EditMessageWidgetData.__returning__ is bool
    assert EditSharedWidgetData.__api_method__ == "editSharedWidgetData"
    assert EditSharedWidgetData.__returning__ is bool
    assert AnswerWidgetQuery.__api_method__ == "answerWidgetQuery"
    assert AnswerWidgetQuery.__returning__ is bool


def test_edit_methods_payloads() -> None:
    by_message = EditMessageWidgetData(chat_id=-100, message_id=77, chat_data="{}")
    assert by_message.model_dump(exclude_none=True) == {
        "chat_id": -100,
        "message_id": 77,
        "chat_data": "{}",
    }
    inline = EditMessageWidgetData(inline_message_id="abc", public_data="")
    assert inline.model_dump(exclude_none=True) == {
        "inline_message_id": "abc",
        "public_data": "",
    }
    shared = EditSharedWidgetData(widget_id="w", public_data='{"p":1}', cache_time=10)
    assert shared.model_dump(exclude_none=True) == {
        "widget_id": "w",
        "public_data": '{"p":1}',
        "cache_time": 10,
    }
    resend = SendSharedWidget(chat_id=1, widget_id="w", chat_data="{}")
    assert resend.model_dump(exclude_none=True) == {
        "chat_id": 1,
        "widget_id": "w",
        "chat_data": "{}",
        "parse_mode": resend.parse_mode,
        "show_caption_above_media": resend.show_caption_above_media,
        "protect_content": resend.protect_content,
    }


def test_widget_query_parses_docs_example() -> None:
    query = WidgetQuery.model_validate(DOCS_QUERY)

    assert query.id == "8123456789012345678"
    assert query.from_user.id == 111111111
    assert query.from_user.first_name == "Alice"
    assert query.chat is not None
    assert query.chat.type == "private"
    assert query.message_id == 4242
    assert query.chat_instance == "901234567890123456"
    assert query.widget_id == "7770011223344556677"
    assert query.data == "refresh"
    assert query.inline_message_id is None
    assert isinstance(query.type, WidgetQueryTypeData)


def test_widget_query_parses_sent_transaction_type() -> None:
    raw = {
        **DOCS_QUERY,
        "type": {
            "type": "sent_transaction",
            "wallet": {"address": "UQx", "amount": "1250000000", "public_key": "a3"},
            "msg_hash": "h",
        },
        "data": "paid:42",
    }

    query = WidgetQuery.model_validate(raw)

    assert isinstance(query.type, WidgetQueryTypeSentTransaction)
    assert query.type.wallet.address == "UQx"
    assert query.type.wallet.state_init is None
    assert query.type.msg_hash == "h"


def test_shared_widget_query_and_widget_parse() -> None:
    shared = SharedWidgetQuery.model_validate(
        {"id": "1", "from": DOCS_QUERY["from"], "data": "v", "widget_id": "w"}
    )
    assert shared.from_user.id == 111111111
    assert shared.widget_id == "w"
    widget = Widget.model_validate({"html": "<b/>", "chat_data": "{}"})
    assert widget.html == "<b/>"
    assert widget.width is None


def test_answer_builds_method_bound_to_the_bot() -> None:
    bot = Bot(token="42:TEST")
    query = WidgetQuery.model_validate(DOCS_QUERY, context={"bot": bot})

    method = query.answer('{"ok":1}')

    assert isinstance(method, AnswerWidgetQuery)
    assert method.widget_query_id == query.id
    assert method.data == '{"ok":1}'
    assert method.bot is bot
    assert query.answer().data is None
