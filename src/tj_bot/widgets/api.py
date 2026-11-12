"""Bot API 10.4 widget types and methods that aiogram 3.31 does not ship yet.

Delete this module once aiogram supports widgets natively
(aiogram/aiogram#2004) and import the same names from ``aiogram.types`` and
``aiogram.methods`` instead.

Source: https://core.telegram.org/bots/api#widgets
"""

from typing import Literal

from aiogram.client.default import Default
from aiogram.methods.base import TelegramMethod
from aiogram.types import (
    Chat,
    ChatIdUnion,
    Message,
    MessageEntity,
    ReplyMarkupUnion,
    ReplyParameters,
    User,
)
from aiogram.types.base import TelegramObject
from pydantic import Field


class Widget(TelegramObject):
    """An HTML widget attached to a message (``Message.widget``)."""

    html: str
    id: str | None = None
    width: int | None = None
    height: int | None = None
    public_data: str | None = None
    chat_data: str | None = None


class WidgetQueryWallet(TelegramObject):
    """TON wallet of the user that signed data or sent a transaction."""

    address: str
    amount: str
    public_key: str | None = None
    state_init: str | None = None


class WidgetQueryTypeData(TelegramObject):
    """A plain data query; ``wallet_card`` (TonWalletCard) stays in ``model_extra``."""

    type: Literal["data"] = "data"


class WidgetQueryTypeSignData(TelegramObject):
    """Data signed by the user's TON wallet for the domain ``bot<bot_id>``."""

    type: Literal["sign_data"] = "sign_data"
    wallet: WidgetQueryWallet
    sign_data: str


class WidgetQueryTypeSentTransaction(TelegramObject):
    """Notification about a transaction sent from the user's TON wallet."""

    type: Literal["sent_transaction"] = "sent_transaction"
    wallet: WidgetQueryWallet
    msg_hash: str


WidgetQueryType = (
    WidgetQueryTypeData | WidgetQueryTypeSignData | WidgetQueryTypeSentTransaction
)


class AnswerWidgetQuery(TelegramMethod[bool]):
    """``answerWidgetQuery``: reply to a query within 15 seconds; returns ``True``.

    ``data`` (0-1024 bytes) reaches only the widget that asked and does not
    change the widget's state.
    """

    __returning__ = bool
    __api_method__ = "answerWidgetQuery"

    widget_query_id: str
    data: str | None = None


class WidgetQuery(TelegramObject):
    """``Update.widget_query``: a request from a widget in a message sent by the bot.

    ``chat``/``message_id`` identify an original message, ``inline_message_id``
    one sent via the bot in inline mode.
    """

    id: str
    from_user: User = Field(..., alias="from")
    chat_instance: str
    data: str
    # the Bot API lists ``type`` as required, but the widgets guide's examples
    # omit it for plain data queries
    type: WidgetQueryType = Field(default_factory=WidgetQueryTypeData)
    chat: Chat | None = None
    message_id: int | None = None
    inline_message_id: str | None = None
    chat_type: str | None = None
    widget_id: str | None = None

    def answer(self, data: str | None = None) -> AnswerWidgetQuery:
        """Build the answer to this query, bound to the bot that received it."""
        return AnswerWidgetQuery(widget_query_id=self.id, data=data).as_(self._bot)


class SharedWidgetQuery(TelegramObject):
    """``Update.shared_widget_query``: a request from a forwarded shared widget."""

    id: str
    from_user: User = Field(..., alias="from")
    data: str
    widget_id: str
    type: WidgetQueryType = Field(default_factory=WidgetQueryTypeData)


class SendWidget(TelegramMethod[Message]):
    """``sendWidget``: send a message with an HTML widget; returns the sent message.

    ``html`` is 1-32768 bytes and at most 4096 bytes zlib-compressed;
    ``public_data``/``chat_data`` are 0-1024 bytes each.
    """

    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: ChatIdUnion
    html: str
    message_thread_id: int | None = None
    width: int | None = None
    height: int | None = None
    public_data: str | None = None
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | Default | None = Default("parse_mode")
    caption_entities: list[MessageEntity] | None = None
    show_caption_above_media: bool | Default | None = Default(
        "show_caption_above_media"
    )
    allow_queries: bool | None = None
    is_shared: bool | None = None
    disable_notification: bool | None = None
    protect_content: bool | Default | None = Default("protect_content")
    allow_paid_broadcast: bool | None = None
    message_effect_id: str | None = None
    reply_parameters: ReplyParameters | None = None
    reply_markup: ReplyMarkupUnion | None = None


class SendSharedWidget(TelegramMethod[Message]):
    """``sendSharedWidget``: send an existing shared widget again by ``widget_id``."""

    __returning__ = Message
    __api_method__ = "sendSharedWidget"

    chat_id: ChatIdUnion
    widget_id: str
    message_thread_id: int | None = None
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | Default | None = Default("parse_mode")
    caption_entities: list[MessageEntity] | None = None
    show_caption_above_media: bool | Default | None = Default(
        "show_caption_above_media"
    )
    disable_notification: bool | None = None
    protect_content: bool | Default | None = Default("protect_content")
    allow_paid_broadcast: bool | None = None
    message_effect_id: str | None = None
    reply_parameters: ReplyParameters | None = None
    reply_markup: ReplyMarkupUnion | None = None


class EditMessageWidgetData(TelegramMethod[bool]):
    """``editMessageWidgetData``: change a widget's data in a message; returns ``True``.

    Pass ``chat_id`` + ``message_id`` or ``inline_message_id``. An omitted
    field keeps its value, an empty string removes it.
    """

    __returning__ = bool
    __api_method__ = "editMessageWidgetData"

    chat_id: ChatIdUnion | None = None
    message_id: int | None = None
    inline_message_id: str | None = None
    public_data: str | None = None
    chat_data: str | None = None


class EditSharedWidgetData(TelegramMethod[bool]):
    """``editSharedWidgetData``: change a shared widget's public data; returns ``True``.

    ``cache_time`` (5-3600 s) is the client-side refresh period, 30 by default.
    """

    __returning__ = bool
    __api_method__ = "editSharedWidgetData"

    widget_id: str
    public_data: str
    cache_time: int | None = None
