from app.models import UserStatus
from app.telebot import handlers


def test_status_text_mapping():
    assert handlers.status_text(UserStatus.NONE) == "Нет заявки"
    assert handlers.status_text(UserStatus.PROCESSING) == "Заявка в обработке"
    assert handlers.status_text(UserStatus.ATTENDEE) == "Участник"
    assert handlers.status_text(UserStatus.WAITLIST) == "Лист ожидания"


def test_notifications_text_variants():
    enabled = handlers.notifications_text(True)
    disabled = handlers.notifications_text(False)

    assert enabled.startswith("[Включены]")
    assert disabled.startswith("[Выключены]")


def test_build_main_keyboard_dynamic_button():
    keyboard = handlers.build_main_keyboard(UserStatus.NONE, event_started=False)
    assert keyboard.keyboard[0][0].text == handlers.MENU_APPLICATION

    keyboard = handlers.build_main_keyboard(UserStatus.PROCESSING, event_started=False)
    assert keyboard.keyboard[0][0].text == handlers.MENU_CANCEL

    keyboard = handlers.build_main_keyboard(UserStatus.ATTENDEE, event_started=True)
    assert keyboard.keyboard[0][0].text == handlers.MENU_FEEDBACK


def test_parse_username_handles_none_and_prefix():
    assert handlers.parse_username(None) is None
    assert handlers.parse_username("@Admin ") == "Admin"


def test_is_admin_respects_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("ADMIN_USERNAMES", "Admin, @Owner")
    handlers.get_settings.cache_clear()

    assert handlers.is_admin("@admin") is True
    assert handlers.is_admin("owner") is True
    assert handlers.is_admin("someone") is False


class _DummyChat:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent_messages.append(text)


class _DummyUser:
    def __init__(self, username: str | None) -> None:
        self.username = username


class _DummyUpdate:
    def __init__(self, username: str | None, chat: _DummyChat | None = None) -> None:
        self.effective_user = _DummyUser(username) if username else None
        self.effective_chat = chat


def test_ensure_admin_sends_denied_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("ADMIN_USERNAMES", "admin")
    handlers.get_settings.cache_clear()

    chat = _DummyChat()
    update = _DummyUpdate(username="guest", chat=chat)
    created: list[object] = []

    def fake_create_task(coro):
        created.append(coro)
        import asyncio

        asyncio.run(coro)
        return None

    monkeypatch.setattr(handlers.asyncio, "create_task", fake_create_task)

    assert handlers.ensure_admin(update) is False
    assert created
    assert chat.sent_messages == ["Unknown command or has no permission"]


def test_ensure_admin_allows_admin(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("ADMIN_USERNAMES", "admin")
    handlers.get_settings.cache_clear()

    chat = _DummyChat()
    update = _DummyUpdate(username="admin", chat=chat)

    assert handlers.ensure_admin(update) is True
    assert chat.sent_messages == []
