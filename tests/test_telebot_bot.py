import app.config as config
from app.telebot import bot as bot_module


def test_build_application_registers_handlers(monkeypatch):
    class DummyBuilder:
        last_token = None

        def token(self, token: str) -> "DummyBuilder":
            DummyBuilder.last_token = token
            return self

        def build(self):
            return object()

    called = {}

    def fake_register(app):
        called["app"] = app

    monkeypatch.setattr(bot_module, "ApplicationBuilder", DummyBuilder)
    monkeypatch.setattr(bot_module.handlers, "register", fake_register)
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    config.get_settings.cache_clear()

    app = bot_module.build_application()

    assert DummyBuilder.last_token == "token"
    assert called["app"] is app
