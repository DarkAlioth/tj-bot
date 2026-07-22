from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.markdown import hcode

echo_router = Router()


@echo_router.message(F.text)
async def echo_hint(message: Message) -> None:
    query = message.text or ""
    text = [
        f"⠀\n⠀Вы написали: {hcode(query)}\n",
        "Для поиска используйте:",
        f"{hcode(f'/s {query}')}\n⠀",
    ]
    await message.answer("\n".join(text))
