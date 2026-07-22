from typing import cast
from unittest.mock import AsyncMock

from aiogram.types import Message

from tj_bot.handlers.echo import echo_hint


async def test_echo_escapes_html_in_user_text() -> None:
    message = AsyncMock()
    message.text = "<b>hello & bye</b>"

    await echo_hint(cast(Message, message))

    sent_text = message.answer.await_args.args[0]
    assert "&lt;b&gt;hello &amp; bye&lt;/b&gt;" in sent_text
    assert "<b>hello" not in sent_text
