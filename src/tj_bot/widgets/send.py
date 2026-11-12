"""Send widgets, edit their state and answer their queries.

Every sent widget is recorded in ``widget_messages`` because a widget query
carries only the request data, never the widget's state.
"""

import json
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from tj_bot.db.repo import TorrentRepo
from tj_bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    SendWidget,
    WidgetQuery,
)
from tj_bot.widgets.assets import load_widget
from tj_bot.widgets.state import dumps_state

logger = logging.getLogger(__name__)

MAX_ANSWER_BYTES = 1024
NOT_MODIFIED = "message is not modified"


class AnswerTooLargeError(ValueError):
    """The serialized answer exceeds the 1024-byte ``answerWidgetQuery`` limit."""


def dumps_answer(payload: dict[str, Any]) -> str:
    """Serialize an answer compactly within the size limit."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    size = len(raw.encode("utf-8"))
    if size > MAX_ANSWER_BYTES:
        raise AnswerTooLargeError(
            f"answer is {size} bytes, limit is {MAX_ANSWER_BYTES}"
        )
    return raw


def answer_json(query: WidgetQuery, payload: dict[str, Any]) -> AnswerWidgetQuery:
    """Answer with compact JSON: a state, ``{"ok":1}`` or ``{"err":…}``."""
    return query.answer(dumps_answer(payload))


def answer_error(query: WidgetQuery, text: str) -> AnswerWidgetQuery:
    return answer_json(query, {"err": text})


async def send_widget(
    bot: Bot,
    chat_id: int,
    name: str,
    state: dict[str, Any],
    caption: str,
    *,
    repo: TorrentRepo,
    kind: str,
    context: dict[str, Any],
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Send ``html/<name>.html`` with ``state`` as chat_data and record its context.

    ``caption`` is HTML (the bot's default parse mode): escape tracker data.
    It is shown above the widget and is what clients without widget support
    display instead of it.
    """
    message = await bot(
        SendWidget(
            chat_id=chat_id,
            html=load_widget(name),
            chat_data=dumps_state(state),
            caption=caption,
            show_caption_above_media=True,
            allow_queries=True,
            reply_markup=reply_markup,
        )
    )
    await repo.save_widget_message(
        chat_id, message.message_id, kind, context, rev=int(state.get("r", 0))
    )
    return message


async def edit_widget_state(
    bot: Bot,
    chat_id: int,
    message_id: int,
    state: dict[str, Any],
    *,
    repo: TorrentRepo | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Write a new chat_data (and context) for a sent widget.

    An edit identical to the current state is reported by Telegram as
    "message is not modified"; it is treated as done, like for text edits.
    """
    try:
        await bot(
            EditMessageWidgetData(
                chat_id=chat_id, message_id=message_id, chat_data=dumps_state(state)
            )
        )
    except TelegramBadRequest as error:
        if NOT_MODIFIED not in str(error):
            raise
        logger.debug("Widget state unchanged for %s/%s", chat_id, message_id)
    if repo is not None:
        await repo.update_widget_message(
            chat_id, message_id, context=context, rev=int(state.get("r", 0))
        )
