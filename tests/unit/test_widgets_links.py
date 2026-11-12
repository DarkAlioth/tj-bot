import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tj_bot.handlers import widgets_links
from tj_bot.handlers.widgets_links import make_send_admin_widget
from tj_bot.services.monitor import MonitorState

STARTED = datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC)


def make_config() -> Any:  # noqa: ANN401  # test double
    config = AsyncMock()
    config.super_admins = []
    return config


async def test_console_shortcut_sends_the_console_or_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = AsyncMock()
    monkeypatch.setattr(widgets_links, "send_console_widget", console)
    bot, repo = AsyncMock(), AsyncMock()

    send = make_send_admin_widget(make_config(), None, MonitorState(), STARTED)
    await send(bot, 5, "console", repo)
    bot.send_message.assert_awaited_once()
    console.assert_not_awaited()

    qbit = AsyncMock()
    send = make_send_admin_widget(make_config(), qbit, MonitorState(), STARTED)
    await send(bot, 5, "console", repo)
    console.assert_awaited_once_with(bot, 5, repo, qbit)


@pytest.mark.parametrize("name", ["dashboard", "users"])
async def test_dashboard_and_users_shortcuts_build_and_send(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    build = AsyncMock(return_value=({"v": 1, "r": 1}, {"a": "h"}))
    sent = AsyncMock()
    monkeypatch.setattr(widgets_links, "build_dashboard", build)
    monkeypatch.setattr(widgets_links, "build_users", build)
    monkeypatch.setattr(widgets_links, "send_widget", sent)
    bot, repo = AsyncMock(), AsyncMock()

    send = make_send_admin_widget(make_config(), None, MonitorState(), STARTED)
    await send(bot, 5, name, repo)

    build.assert_awaited_once()
    assert sent.await_args is not None
    assert sent.await_args.args[:3] == (bot, 5, name)
    assert sent.await_args.kwargs["kind"] == name


def test_hub_status_reflects_the_last_pass() -> None:
    state = MonitorState()
    assert state.hub_status() is None
    state.checked_at = 1_794_500_000
    state.qbit_ok = False
    status = state.hub_status()
    assert status is not None
    assert status.jackett_ok is True and status.qbit_ok is False
    assert status.at.year == 2026
    state.problems["jackett"] = AsyncMock()
    state.qbit_ok = None
    status = state.hub_status()
    assert status is not None
    assert status.jackett_ok is False and status.qbit_ok is True
