from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast

from aiogram.types import Message

from tgbot.config import Config
from tgbot.filters.admin import AdminFilter


@dataclass
class FakeTgBot:
    admin_ids: list[int] = field(default_factory=lambda: [111, 222])


@dataclass
class FakeConfig:
    tg_bot: FakeTgBot = field(default_factory=FakeTgBot)


def make_message(user_id: int) -> Message:
    return cast(Message, SimpleNamespace(from_user=SimpleNamespace(id=user_id)))


async def test_admin_passes() -> None:
    config = cast(Config, FakeConfig())
    assert await AdminFilter()(make_message(111), config) is True


async def test_non_admin_rejected() -> None:
    config = cast(Config, FakeConfig())
    assert await AdminFilter()(make_message(999), config) is False
