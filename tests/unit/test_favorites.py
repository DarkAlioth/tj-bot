import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram import types
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.favorites import (
    Fav,
    download_favorite,
    favorite_to_server,
    open_favorite,
    remove_favorite,
    render_favorites,
    show_favorites,
    toggle_favorite,
)
from tj_bot.handlers.user import Dlt
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient


def make_favorite(favorite_id: int = 7) -> MagicMock:
    favorite = MagicMock()
    favorite.id = favorite_id
    favorite.title = "Movie 1080p"
    favorite.category = "Movies"
    favorite.tracker = "rutracker"
    favorite.details_url = "https://tracker.example/1"
    favorite.download_url = "http://jackett:9117/dl/1"
    favorite.seeders = 5
    favorite.size = 2 * 1024**3
    favorite.published_at = datetime.date(2026, 7, 1)
    favorite.created_at = datetime.datetime(2026, 7, 22, tzinfo=datetime.UTC)
    return favorite


def make_config(admin: bool = False, qbit_enabled: bool = False) -> AppConfig:
    config = MagicMock()
    config.is_admin = lambda _uid: admin
    config.qbit_enabled = qbit_enabled
    config.settings.qbit_category = "tj-bot"
    return cast(AppConfig, config)


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    query.from_user = MagicMock()
    query.from_user.id = 111
    query.data = None
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    message.answer_document = AsyncMock()
    message.reply_markup = None
    query.message = message
    query.answer = AsyncMock()
    return query


def card_markup(fav_data: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💾 Скачать", callback_data="dlt:download:h1"
                ),
                types.InlineKeyboardButton(text="☆ Сохранить", callback_data=fav_data),
            ]
        ]
    )


async def test_toggle_adds_then_removes_and_swaps_button() -> None:
    query = make_query()
    data = Dlt(type="fav", hash="h1")
    query.data = data.pack()
    query.message.reply_markup = card_markup(data.pack())
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = MagicMock(hash="h1")
    repo.add_favorite.return_value = True

    await toggle_favorite(query, data, repo)
    assert "⭐" in query.answer.await_args.args[0]
    swapped = query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert swapped.inline_keyboard[0][1].text == "⭐ Сохранено"
    assert swapped.inline_keyboard[0][0].text == "💾 Скачать"  # untouched

    query.message.reply_markup = swapped
    repo.add_favorite.return_value = False
    await toggle_favorite(query, data, repo)
    repo.remove_favorite_by_hash.assert_awaited_once_with(111, "h1")
    assert "Убрано" in query.answer.await_args.args[0]
    swapped_back = query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert swapped_back.inline_keyboard[0][1].text == "☆ Сохранить"


async def test_toggle_stale_card_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = None

    await toggle_favorite(query, Dlt(type="fav", hash="h1"), repo)

    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.add_favorite.assert_not_awaited()


async def test_favorites_command_renders_numbered_list() -> None:
    message = AsyncMock(spec=Message)
    message.from_user = MagicMock()
    message.from_user.id = 111
    message.answer = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_favorites.return_value = 12
    repo.list_favorites.return_value = [make_favorite(i) for i in range(10)]

    await show_favorites(cast(Message, message), repo)

    text = message.answer.await_args.args[0]
    assert "Избранное</b> — 12" in text
    assert text.count("Movie 1080p") == 10
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    numbers = [b.text for row in keyboard.inline_keyboard[:-1] for b in row]
    assert numbers == [str(n) for n in range(1, 11)]
    nav = keyboard.inline_keyboard[-1]
    assert [b.text for b in nav] == ["➡"]


async def test_favorites_empty_message() -> None:
    message = AsyncMock(spec=Message)
    message.from_user = MagicMock()
    message.from_user.id = 111
    message.answer = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_favorites.return_value = 0
    repo.list_favorites.return_value = []

    await show_favorites(cast(Message, message), repo)

    assert "пусто" in message.answer.await_args.args[0]


async def test_open_favorite_shows_card_with_admin_actions() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_favorite.return_value = make_favorite()

    await open_favorite(
        query, Fav(a="op", id=7), repo, make_config(admin=True, qbit_enabled=True)
    )

    repo.get_favorite.assert_awaited_once_with(111, 7)
    text = query.message.edit_text.await_args.args[0]
    assert "Movie 1080p" in text
    assert "Сохранено" in text
    keyboard = query.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["💾 Скачать", "⬇️ На сервер", "🗑 Удалить", "↩️ Список"]


async def test_open_favorite_hides_server_button_for_users() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_favorite.return_value = make_favorite()

    await open_favorite(query, Fav(a="op", id=7), repo, make_config())

    keyboard = query.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert "⬇️ На сервер" not in labels


async def test_open_missing_favorite_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_favorite.return_value = None

    await open_favorite(query, Fav(a="op", id=7), repo, make_config())

    assert query.answer.await_args.kwargs.get("show_alert") is True
    query.message.edit_text.assert_not_awaited()


async def test_download_favorite_sends_document() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_favorite.return_value = make_favorite()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"data"

    await download_favorite(query, Fav(a="dl", id=7), repo, jackett)

    query.message.answer_document.assert_awaited_once()
    repo.record_download.assert_awaited_once_with(111, "Movie 1080p", "chat")


async def test_download_favorite_reports_stale_link() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_favorite.return_value = make_favorite()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = JackettError("gone")

    await download_favorite(query, Fav(a="dl", id=7), repo, jackett)

    assert "устареть" in query.message.answer.await_args.args[0]
    repo.record_download.assert_not_awaited()


async def test_favorite_to_server_opens_category_menu() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_favorite.return_value = make_favorite()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.categories.return_value = {"tj-bot": {"savePath": "/dl"}}
    qbit.free_space.return_value = 100 * 1024**3

    await favorite_to_server(
        query, Fav(a="sv", id=7), repo, qbit, make_config(admin=True)
    )

    body = query.message.answer.await_args.args[0]
    assert "Выберите категорию" in body


async def test_favorite_to_server_denied_for_users() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)

    await favorite_to_server(query, Fav(a="sv", id=7), repo, None, make_config())

    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.get_favorite.assert_not_awaited()


async def test_remove_favorite_rerenders_list() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_favorites.return_value = 0
    repo.list_favorites.return_value = []

    await remove_favorite(query, Fav(a="rm", id=7), repo)

    repo.remove_favorite.assert_awaited_once_with(111, 7)
    assert "пусто" in query.message.edit_text.await_args.args[0]


async def test_render_favorites_second_page_nav() -> None:
    message = AsyncMock(spec=Message)
    message.edit_text = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_favorites.return_value = 25
    repo.list_favorites.return_value = [make_favorite(i) for i in range(10)]

    await render_favorites(cast(Message, message), repo, 111, page=1, edit=True)

    assert repo.list_favorites.await_args.args == (111, 10, 10)
    nav = message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[-1]
    assert [b.text for b in nav] == ["⬅", "➡"]
