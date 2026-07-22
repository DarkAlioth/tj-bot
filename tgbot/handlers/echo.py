from aiogram import types, Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.utils.markdown import hcode

echo_router = Router()


@echo_router.message(F.text, StateFilter(None))
async def bot_echo(message: types.Message):
    text = [
        f"⠀\n⠀Вы написали: {message.text}\n",
        f"Для поиска используйте:",
        f"<code>/s {message.text}</code>\n⠀"
    ]

    await message.answer("\n".join(text))


@echo_router.message(F.text)
async def bot_echo_all(message: types.Message, state: FSMContext):
    state_name = await state.get_state()
    text = [
        f"{hcode(state_name)}",
        ":",
        hcode(message.text),
    ]
    await message.answer("\n".join(text))
