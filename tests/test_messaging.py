import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.error import TelegramError

from app.services import messaging


class FakeResult:
    def __init__(self, users):
        self._users = users

    def scalars(self):
        return self

    def all(self):
        return self._users


class FakeSession:
    def __init__(self, users):
        self._users = users
        self.executed = []

    def execute(self, statement):
        self.executed.append(statement)
        return FakeResult(self._users)


def test_broadcast_message_delivers_and_unsubscribes():
    users = [
        SimpleNamespace(id=1, telegram_id=100, is_subscribed=True),
        SimpleNamespace(id=2, telegram_id=None, is_subscribed=True),
        SimpleNamespace(id=3, telegram_id=200, is_subscribed=True),
    ]
    session = FakeSession(users)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[None, TelegramError("failed")]))

    delivered = asyncio.run(messaging.broadcast_message(session, bot, "Hello"))

    assert delivered == 1
    assert users[0].is_subscribed is True
    assert users[1].is_subscribed is True
    assert users[2].is_subscribed is False
    assert bot.send_message.await_count == 2
