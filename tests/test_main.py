import asyncio

import app.config as config
import app.main as main


class DummyUpdater:
    def __init__(self) -> None:
        self.running = False

    async def start_polling(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False


class DummyApplication:
    def __init__(self) -> None:
        self.bot = object()
        self.running = False
        self.updater = DummyUpdater()
        self.post_init = None
        self.post_stop = None
        self.post_shutdown = None

    async def initialize(self) -> None:
        return None

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def shutdown(self) -> None:
        return None


class DummyEvent:
    async def wait(self) -> None:
        return None


def test_run_completes_shutdown(monkeypatch):
    settings = config.Settings(
        telegram_token="token",
        enable_admin_web=False,
    )

    async def post_init(app):
        return None

    async def post_stop(app):
        return None

    async def post_shutdown(app):
        return None

    app = DummyApplication()
    app.post_init = post_init
    app.post_stop = post_stop
    app.post_shutdown = post_shutdown

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "ensure_schema", lambda: None)
    monkeypatch.setattr(main, "build_application", lambda: app)
    monkeypatch.setattr(main, "config_path", lambda: None)
    monkeypatch.setattr(main.asyncio, "Event", DummyEvent)

    asyncio.run(main.run())
