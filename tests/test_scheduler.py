import asyncio
from contextlib import contextmanager

import app.config as config
from app import scheduler


class DummyPost:
    def __init__(self, post_id: int) -> None:
        self.id = post_id


def test_process_scheduled_posts_no_pending(monkeypatch):
    @contextmanager
    def fake_session_scope():
        yield object()

    monkeypatch.setattr(scheduler, "session_scope", fake_session_scope)
    monkeypatch.setattr(scheduler, "get_pending_posts", lambda session: [])

    called = {"broadcast": 0}

    async def fake_broadcast(session, bot, post) -> None:
        called["broadcast"] += 1

    monkeypatch.setattr(scheduler, "broadcast_post", fake_broadcast)

    asyncio.run(scheduler.process_scheduled_posts(bot=object()))
    assert called["broadcast"] == 0


def test_process_scheduled_posts_handles_exceptions(monkeypatch):
    @contextmanager
    def fake_session_scope():
        yield object()

    monkeypatch.setattr(scheduler, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        scheduler, "get_pending_posts", lambda session: [DummyPost(1), DummyPost(2)]
    )

    async def fake_broadcast(session, bot, post) -> None:
        if post.id == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "broadcast_post", fake_broadcast)

    asyncio.run(scheduler.process_scheduled_posts(bot=object()))


def test_start_and_stop_scheduler(monkeypatch):
    settings = config.Settings(telegram_token="token", scheduler_interval_seconds=5)

    class DummyScheduler:
        def __init__(self, event_loop=None) -> None:
            self.event_loop = event_loop
            self.jobs = []

        def add_job(self, func, trigger, seconds: int) -> None:
            self.jobs.append((func, trigger, seconds))

        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            return None

    monkeypatch.setattr(scheduler, "AsyncIOScheduler", DummyScheduler)

    async def _run():
        sched = scheduler.start_scheduler(settings, bot=object())
        assert sched.jobs[0][1] == "interval"
        assert sched.jobs[0][2] == 5
        scheduler.stop_scheduler(sched)

    asyncio.run(_run())
