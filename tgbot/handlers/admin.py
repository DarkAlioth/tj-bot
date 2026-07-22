from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.filters.command import Command, CommandStart

from tgbot.filters.admin import AdminFilter

admin_router = Router()
admin_router.message.filter(AdminFilter())


@admin_router.message(CommandStart(deep_link=True))
async def admin_start(message: Message, command):
    text = [
        command.args
    ]
    await message.answer("\n".join(text), parse_mode=ParseMode.HTML)


@admin_router.message(Command("d"))
async def dl_torrent(message: Message, command):
    print(command.args)
    await message.answer(f"Your payload: {command.args}")
