import datetime
import html
import logging

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.models import BotUser
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.services.formatting import padded

logger = logging.getLogger(__name__)

users_router = Router()
users_router.message.filter(AdminOnly())
users_router.callback_query.filter(AdminOnly())


class Usr(CallbackData, prefix="usr"):
    a: str
    uid: int


def user_label(user: BotUser) -> str:
    flag = "🚫" if user.blocked else ("⭐" if user.admin else "👤")
    if user.username:
        name = f"@{user.username}"
    else:
        name = user.full_name or str(user.user_id)
    return f"{flag} {name}"


def format_dt(value: datetime.datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def build_user_card(
    user: BotUser, config: AppConfig
) -> tuple[str, types.InlineKeyboardMarkup]:
    is_super = config.is_super_admin(user.user_id)
    if user.admin:
        status = "⭐ админ"
    elif user.blocked:
        status = "🚫 заблокирован"
    else:
        status = "👤 обычный"
    if is_super:
        status = "⭐ супер-админ (.env)"
    lines = [
        f"👤 <b>{html.escape(user.full_name or 'без имени')}</b>",
        f"@{html.escape(user.username)}" if user.username else "без username",
        f"ID: <code>{user.user_id}</code>",
        f"Статус: {status}",
        f"Впервые: {format_dt(user.first_seen)}",
        f"Активность: {format_dt(user.last_seen)}",
    ]
    rows: list[list[types.InlineKeyboardButton]] = []
    if not is_super:
        if user.blocked:
            rows.append([_btn("✅ Разблокировать", "unblock", user.user_id)])
        elif not user.admin:
            rows.append([_btn("🚫 Заблокировать", "block", user.user_id)])
        if user.admin:
            rows.append([_btn("⬇️ Снять админа", "demote", user.user_id)])
        else:
            rows.append([_btn("⭐ В админы", "promote", user.user_id)])
    rows.append([_btn("📋 История", "activity", user.user_id)])
    rows.append([_btn("◀️ К списку", "list", user.user_id)])
    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, action: str, uid: int) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Usr(a=action, uid=uid).pack()
    )


async def build_user_list(repo: TorrentRepo) -> tuple[str, types.InlineKeyboardMarkup]:
    users = await repo.list_recent_users()
    if not users:
        return "Пользователей пока нет.", types.InlineKeyboardMarkup(inline_keyboard=[])
    rows = [
        [
            types.InlineKeyboardButton(
                text=user_label(user),
                callback_data=Usr(a="card", uid=user.user_id).pack(),
            )
        ]
        for user in users
    ]
    return (
        "👥 <b>Пользователи</b> — недавняя активность.\nНажмите для управления:",
        types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


@users_router.message(Command("users"))
async def list_users(message: Message, repo: TorrentRepo) -> None:
    text, keyboard = await build_user_list(repo)
    await message.answer(padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard)


@users_router.callback_query(Usr.filter(F.a == "list"))
async def back_to_list(query: CallbackQuery, repo: TorrentRepo) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    text, keyboard = await build_user_list(repo)
    await query.answer()
    await message.edit_text(
        padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


@users_router.callback_query(Usr.filter(F.a == "card"))
async def show_user(
    query: CallbackQuery, callback_data: Usr, repo: TorrentRepo, config: AppConfig
) -> None:
    message = query.message
    user = await repo.get_user(callback_data.uid)
    if user is None or not isinstance(message, Message):
        await query.answer("Пользователь не найден", show_alert=True)
        return
    text, keyboard = build_user_card(user, config)
    await query.answer()
    await message.edit_text(
        padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


@users_router.callback_query(Usr.filter(F.a.in_({"block", "unblock"})))
async def toggle_block(
    query: CallbackQuery, callback_data: Usr, repo: TorrentRepo, config: AppConfig
) -> None:
    message = query.message
    if config.is_super_admin(callback_data.uid):
        await query.answer("Супер-админа нельзя заблокировать", show_alert=True)
        return
    blocked = callback_data.a == "block"
    await repo.set_blocked(callback_data.uid, blocked)
    await query.answer("Заблокирован" if blocked else "Разблокирован")
    user = await repo.get_user(callback_data.uid)
    if user is not None and isinstance(message, Message):
        text, keyboard = build_user_card(user, config)
        await message.edit_text(
            padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
        )


@users_router.callback_query(Usr.filter(F.a.in_({"promote", "demote"})))
async def toggle_admin(
    query: CallbackQuery, callback_data: Usr, repo: TorrentRepo, config: AppConfig
) -> None:
    message = query.message
    if config.is_super_admin(callback_data.uid):
        await query.answer("Супер-админ задан в .env", show_alert=True)
        return
    promote = callback_data.a == "promote"
    await repo.set_admin(callback_data.uid, promote)
    # keep the in-memory admin set live so access changes take effect at once
    if promote:
        config.dynamic_admins.add(callback_data.uid)
    else:
        config.dynamic_admins.discard(callback_data.uid)
    await query.answer("Назначен админом" if promote else "Снят с админа")
    user = await repo.get_user(callback_data.uid)
    if user is not None and isinstance(message, Message):
        text, keyboard = build_user_card(user, config)
        await message.edit_text(
            padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
        )


KIND_LABELS = {"chat": "📥 в чат", "server": "🖥 на сервер"}


@users_router.callback_query(Usr.filter(F.a == "activity"))
async def show_activity(
    query: CallbackQuery, callback_data: Usr, repo: TorrentRepo
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    uid = callback_data.uid
    searches, downloads = await repo.user_activity_counts(uid)
    recent_searches = await repo.get_user_searches(uid, limit=10)
    recent_downloads = await repo.get_user_downloads(uid, limit=10)
    lines = [f"📋 <b>Активность</b> — поисков: {searches}, скачиваний: {downloads}\n"]
    lines.append("🔎 <b>Последние запросы:</b>")
    if recent_searches:
        lines.extend(
            f"• {format_dt(when)} — <code>{html.escape(text)}</code>"
            for text, when in recent_searches
        )
    else:
        lines.append("<i>нет</i>")
    lines.append("\n⬇️ <b>Последние скачивания:</b>")
    if recent_downloads:
        lines.extend(
            f"• {format_dt(when)} · {KIND_LABELS.get(kind, kind)} — "
            f"{html.escape(title)}"
            for title, kind, when in recent_downloads
        )
    else:
        lines.append("<i>нет</i>")
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[_btn("◀️ К пользователю", "card", uid)]]
    )
    await query.answer()
    await message.edit_text(
        padded("\n".join(lines)), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
