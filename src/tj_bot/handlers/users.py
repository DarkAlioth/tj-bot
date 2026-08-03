import datetime
import html
import logging

from aiogram import Bot, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message

from tj_bot.commands import set_user_commands
from tj_bot.config import AppConfig
from tj_bot.db.models import BotUser
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.services.formatting import padded

logger = logging.getLogger(__name__)

users_router = Router()
users_router.message.filter(AdminOnly())
users_router.callback_query.filter(AdminOnly())

USERS_PAGE_SIZE = 10

GROUP_ALL = "a"
GROUP_REGULAR = "r"
GROUP_ADMINS = "m"
GROUP_BLOCKED = "b"
# short button labels; the header uses the descriptive form
TAB_LABELS = {
    GROUP_ALL: "👥 Все",
    GROUP_REGULAR: "👤 Обычные",
    GROUP_ADMINS: "⭐ Админы",
    GROUP_BLOCKED: "🚫 Блок",
}
HEADER_LABELS = {
    GROUP_ALL: "все",
    GROUP_REGULAR: "обычные",
    GROUP_ADMINS: "админы",
    GROUP_BLOCKED: "заблокированные",
}
REPO_GROUPS = {
    GROUP_ALL: "all",
    GROUP_REGULAR: "regular",
    GROUP_ADMINS: "admins",
    GROUP_BLOCKED: "blocked",
}


class Usr(CallbackData, prefix="usr"):
    """Legacy card/action callback from messages sent before the group list."""

    a: str
    uid: int


class UsrL(CallbackData, prefix="usrl"):
    """User list navigation: group tab + page."""

    g: str
    p: int


class Us2(CallbackData, prefix="us2"):
    """Card action carrying the list position it was opened from."""

    a: str
    uid: int
    g: str
    p: int


def user_label(user: BotUser, config: AppConfig) -> str:
    if user.blocked:
        flag = "🚫"
    elif config.is_super_admin(user.user_id):
        flag = "👑"
    elif user.admin:
        flag = "⭐"
    else:
        flag = "👤"
    if user.username:
        name = f"@{user.username}"
    else:
        name = user.full_name or str(user.user_id)
    return f"{flag} {name}"


def format_dt(value: datetime.datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _card_btn(
    text: str, action: str, uid: int, group: str, page: int
) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Us2(a=action, uid=uid, g=group, p=page).pack()
    )


def build_user_card(
    user: BotUser, config: AppConfig, group: str = GROUP_ALL, page: int = 0
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
        "⠀",
        f"@{html.escape(user.username)}" if user.username else "без username",
        f"ID: <code>{user.user_id}</code>",
        f"Статус: {status}",
        f"Впервые: {format_dt(user.first_seen)}",
        f"Активность: {format_dt(user.last_seen)}",
    ]
    uid = user.user_id
    rows: list[list[types.InlineKeyboardButton]] = []
    if not is_super:
        if user.blocked:
            rows.append([_card_btn("✅ Разблокировать", "unblock", uid, group, page)])
        elif not user.admin:
            rows.append([_card_btn("🚫 Заблокировать", "block", uid, group, page)])
        if user.admin:
            rows.append([_card_btn("⬇️ Снять админа", "demote", uid, group, page)])
        else:
            rows.append([_card_btn("⭐ В админы", "promote", uid, group, page)])
    rows.append([_card_btn("📋 История", "activity", uid, group, page)])
    rows.append(
        [
            types.InlineKeyboardButton(
                text="◀️ К списку", callback_data=UsrL(g=group, p=page).pack()
            )
        ]
    )
    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=rows)


async def build_user_list(
    repo: TorrentRepo, config: AppConfig, group: str, page: int
) -> tuple[str, types.InlineKeyboardMarkup]:
    if group not in REPO_GROUPS:
        group = GROUP_ALL
    total = await repo.count_users(REPO_GROUPS[group], config.super_admins)
    pages = max(1, -(-total // USERS_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    users = await repo.list_users_page(
        USERS_PAGE_SIZE,
        page * USERS_PAGE_SIZE,
        REPO_GROUPS[group],
        config.super_admins,
    )
    tabs = [
        types.InlineKeyboardButton(
            text=("• " + label) if code == group else label,
            callback_data=UsrL(g=code, p=0).pack(),
        )
        for code, label in TAB_LABELS.items()
    ]
    rows: list[list[types.InlineKeyboardButton]] = [tabs]
    rows.extend(
        [
            types.InlineKeyboardButton(
                text=user_label(user, config),
                callback_data=Us2(a="card", uid=user.user_id, g=group, p=page).pack(),
            )
        ]
        for user in users
    )
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                types.InlineKeyboardButton(
                    text="⬅", callback_data=UsrL(g=group, p=page - 1).pack()
                )
            )
        nav.append(
            types.InlineKeyboardButton(
                text=f"{page + 1}/{pages}", callback_data=UsrL(g=group, p=page).pack()
            )
        )
        if page + 1 < pages:
            nav.append(
                types.InlineKeyboardButton(
                    text="➡", callback_data=UsrL(g=group, p=page + 1).pack()
                )
            )
        rows.append(nav)
    header = f"👥 <b>Пользователи</b> — {HEADER_LABELS[group]}: {total}"
    body = "Нажмите для управления:" if users else "В этой группе пока никого нет."
    return (
        f"{header}\n⠀\n{body}",
        types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


@users_router.message(Command("users"))
async def list_users(message: Message, repo: TorrentRepo, config: AppConfig) -> None:
    text, keyboard = await build_user_list(repo, config, GROUP_ALL, 0)
    await message.answer(padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard)


@users_router.callback_query(UsrL.filter())
async def users_page(
    query: CallbackQuery, callback_data: UsrL, repo: TorrentRepo, config: AppConfig
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    text, keyboard = await build_user_list(
        repo, config, callback_data.g, callback_data.p
    )
    await query.answer()
    await message.edit_text(
        padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def render_user_card(
    query: CallbackQuery,
    repo: TorrentRepo,
    config: AppConfig,
    uid: int,
    group: str,
    page: int,
) -> None:
    message = query.message
    user = await repo.get_user(uid)
    if user is None or not isinstance(message, Message):
        await query.answer("Пользователь не найден", show_alert=True)
        return
    text, keyboard = build_user_card(user, config, group, page)
    await query.answer()
    await message.edit_text(
        padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def apply_block_toggle(
    query: CallbackQuery,
    repo: TorrentRepo,
    config: AppConfig,
    uid: int,
    blocked: bool,
    group: str,
    page: int,
) -> None:
    message = query.message
    if config.is_super_admin(uid):
        await query.answer("Супер-админа нельзя заблокировать", show_alert=True)
        return
    await repo.set_blocked(uid, blocked)
    await query.answer("Заблокирован" if blocked else "Разблокирован")
    user = await repo.get_user(uid)
    if user is not None and isinstance(message, Message):
        text, keyboard = build_user_card(user, config, group, page)
        await message.edit_text(
            padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
        )


async def apply_admin_toggle(
    query: CallbackQuery,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    uid: int,
    promote: bool,
    group: str,
    page: int,
) -> None:
    message = query.message
    if config.is_super_admin(uid):
        await query.answer("Супер-админ задан в .env", show_alert=True)
        return
    await repo.set_admin(uid, promote)
    # keep the in-memory admin set live so access changes take effect at once
    if promote:
        config.dynamic_admins.add(uid)
    else:
        config.dynamic_admins.discard(uid)
    # re-publish the command menu so the role change is visible immediately
    await set_user_commands(bot, uid, promote)
    await query.answer("Назначен админом" if promote else "Снят с админа")
    user = await repo.get_user(uid)
    if user is not None and isinstance(message, Message):
        text, keyboard = build_user_card(user, config, group, page)
        await message.edit_text(
            padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
        )


KIND_LABELS = {"chat": "📥 в чат", "server": "🖥 на сервер"}


async def render_user_activity(
    query: CallbackQuery, repo: TorrentRepo, uid: int, group: str, page: int
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    searches, downloads = await repo.user_activity_counts(uid)
    recent_searches = await repo.get_user_searches(uid, limit=10)
    recent_downloads = await repo.get_user_downloads(uid, limit=10)
    lines = [
        f"📋 <b>Активность</b> — поисков: {searches}, скачиваний: {downloads}",
        "⠀",
    ]
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
        inline_keyboard=[[_card_btn("◀️ К пользователю", "card", uid, group, page)]]
    )
    await query.answer()
    await message.edit_text(
        padded("\n".join(lines)), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def dispatch_action(
    query: CallbackQuery,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    action: str,
    uid: int,
    group: str,
    page: int,
) -> None:
    if action == "card":
        await render_user_card(query, repo, config, uid, group, page)
    elif action in ("block", "unblock"):
        await apply_block_toggle(
            query, repo, config, uid, action == "block", group, page
        )
    elif action in ("promote", "demote"):
        await apply_admin_toggle(
            query, repo, config, bot, uid, action == "promote", group, page
        )
    elif action == "activity":
        await render_user_activity(query, repo, uid, group, page)
    else:
        await query.answer()


@users_router.callback_query(Us2.filter())
async def user_action(
    query: CallbackQuery,
    callback_data: Us2,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> None:
    await dispatch_action(
        query,
        repo,
        config,
        bot,
        callback_data.a,
        callback_data.uid,
        callback_data.g,
        callback_data.p,
    )


@users_router.callback_query(Usr.filter())
async def legacy_user_action(
    query: CallbackQuery,
    callback_data: Usr,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> None:
    """Buttons from cards sent before groups existed fall back to Все/стр.1."""
    if callback_data.a == "list":
        await users_page(query, UsrL(g=GROUP_ALL, p=0), repo, config)
        return
    await dispatch_action(
        query, repo, config, bot, callback_data.a, callback_data.uid, GROUP_ALL, 0
    )
