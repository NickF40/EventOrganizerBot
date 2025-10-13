import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.error import TelegramError

from app.models import ScheduledPost
from app.services import posts
from tests.test_events import make_session


class StubSession:
    def __init__(self):
        self.added: list[ScheduledPost] = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for index, obj in enumerate(self.added, start=1):
            obj.id = index


def test_schedule_post_normalizes_timezone():
    session = StubSession()
    send_at = datetime(2024, 5, 1, 12, 0, 0)

    post = posts.schedule_post(session, title="Update", content="Body", send_at=send_at)

    assert post.id == 1
    assert post.send_at.tzinfo is timezone.utc


def test_get_pending_posts_filters_by_time():
    with make_session() as session:
        now = datetime(2024, 5, 1, 13, 0, tzinfo=timezone.utc)

        ready = ScheduledPost(title="Ready", content="Soon", send_at=now - timedelta(minutes=5))
        upcoming = ScheduledPost(title="Later", content="Later", send_at=now + timedelta(hours=1))

        session.add_all([ready, upcoming])
        session.commit()

        pending = posts.get_pending_posts(session, now=now)

        assert [p.title for p in pending] == ["Ready"]


def test_broadcast_post_marks_sent_and_unsubscribes():
    users = [
        SimpleNamespace(id=1, telegram_id=101, is_subscribed=True),
        SimpleNamespace(id=2, telegram_id=202, is_subscribed=True),
    ]

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return users

    class FakeSession:
        def execute(self, statement):
            self.statement = statement
            return FakeResult()

    session = FakeSession()
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[None, TelegramError("oops")])
    )
    post = SimpleNamespace(id=5, title="Title", content="Content", sent_at=None)

    asyncio.run(posts.broadcast_post(session, bot, post))

    assert post.sent_at is not None
    assert users[0].is_subscribed is True
    assert users[1].is_subscribed is False
