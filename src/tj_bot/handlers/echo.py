from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.markdown import hcode

from tj_bot.services.formatting import padded

echo_router = Router()


@echo_router.message(F.text)
async def echo_hint(message: Message) -> None:
    query = message.text or ""
    text = [
        f"Вы написали: {hcode(query)}",
        "⠀",
        "Для поиска используйте:",
        hcode(f"/s {query}"),
    ]
    await message.answer(padded("\n".join(text)))
