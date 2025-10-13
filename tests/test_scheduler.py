import asyncio
from types import SimpleNamespace

from app import scheduler


class DummySessionScope:
    def __init__(self, session):
        self._session = session
        self.entered = False
        self.exited = False

    def __call__(self):
        return self

    def __enter__(self):
        self.entered = True
        return self._session

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        return False


def test_process_scheduled_posts_no_pending(monkeypatch):
    session = object()
    scope = DummySessionScope(session)
    monkeypatch.setattr(scheduler, "session_scope", scope)
    monkeypatch.setattr(scheduler, "get_pending_posts", lambda _session: [])
    broadcast_calls: list[int] = []

    async def fake_broadcast(session_arg, bot_arg, post_arg):
        broadcast_calls.append(post_arg.id)

    monkeypatch.setattr(scheduler, "broadcast_post", fake_broadcast)

    asyncio.run(scheduler.process_scheduled_posts(bot=object()))

    assert scope.entered and scope.exited
    assert broadcast_calls == []


def test_process_scheduled_posts_handles_errors(monkeypatch):
    session = object()
    scope = DummySessionScope(session)
    monkeypatch.setattr(scheduler, "session_scope", scope)

    posts = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    monkeypatch.setattr(scheduler, "get_pending_posts", lambda _session: posts)

    calls: list[int] = []

    async def fake_broadcast(_session, _bot, post):
        calls.append(post.id)
        if post.id == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "broadcast_post", fake_broadcast)

    asyncio.run(scheduler.process_scheduled_posts(bot=object()))

    assert calls == [1, 2]


def test_start_and_stop_scheduler(monkeypatch):
    recorded_loop = object()
    monkeypatch.setattr(scheduler.asyncio, "get_running_loop", lambda: recorded_loop)

    scheduled_jobs: list[dict[str, object]] = []

    class DummyScheduler:
        def __init__(self, *, event_loop):
            self.event_loop = event_loop
            self.started = False
            self.shutdown_called = None

        def add_job(self, func, trigger, seconds):
            scheduled_jobs.append({"func": func, "trigger": trigger, "seconds": seconds})

        def start(self):
            self.started = True

        def shutdown(self, wait):
            self.shutdown_called = wait

    monkeypatch.setattr(scheduler, "AsyncIOScheduler", DummyScheduler)

    called = {}

    async def fake_process(bot):
        called["value"] = bot

    monkeypatch.setattr(scheduler, "process_scheduled_posts", fake_process)

    dummy_bot = object()
    settings = SimpleNamespace(scheduler_interval_seconds=30, timezone="UTC")

    sched = scheduler.start_scheduler(settings, dummy_bot)

    assert isinstance(sched, DummyScheduler)
    assert sched.event_loop is recorded_loop
    assert sched.started
    assert len(scheduled_jobs) == 1

    job_func = scheduled_jobs[0]["func"]
    asyncio.run(job_func())
    assert called["value"] is dummy_bot

    scheduler.stop_scheduler(sched)
    assert sched.shutdown_called is False
