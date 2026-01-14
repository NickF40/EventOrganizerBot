import asyncio
import csv
import importlib
import io
from dataclasses import dataclass
from typing import Any

import app.config as config
import app.localization as localization
from sqlalchemy import select


@dataclass
class DummyUser:
    id: int
    username: str | None


class DummyMessage:
    def __init__(self, text: str | None = None, chat_id: int = 1, message_id: int = 11):
        self.text = text
        self.chat_id = chat_id
        self.message_id = message_id
        self.document = None
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class DummyDocument:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class DummyFile:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self._data)


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.copied_messages: list[dict[str, Any]] = []
        self.files: dict[str, DummyFile] = {}

    async def send_message(self, chat_id: int, text: str, reply_markup: Any | None = None) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int) -> None:
        self.copied_messages.append(
            {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
        )

    async def get_file(self, file_id: str) -> DummyFile:
        return self.files[file_id]


class DummyChat:
    def __init__(self, chat_id: int = 1, bot: DummyBot | None = None) -> None:
        self.id = chat_id
        self.bot = bot
        self.sent_messages: list[tuple[str, Any | None]] = []
        self.sent_documents: list[tuple[bytes, str]] = []

    async def send_message(self, text: str, reply_markup: Any | None = None) -> None:
        self.sent_messages.append((text, reply_markup))

    async def send_document(self, document: Any, filename: str) -> None:
        self.sent_documents.append((document.getvalue(), filename))


class DummyUpdate:
    def __init__(
        self,
        user: DummyUser | None,
        chat: DummyChat | None,
        message: DummyMessage | None = None,
        update_id: int = 1,
    ) -> None:
        self.effective_user = user
        self.effective_chat = chat
        self.message = message
        self.update_id = update_id


class DummyContext:
    def __init__(self, bot: DummyBot) -> None:
        self.bot = bot
        self.user_data: dict[str, Any] = {}


def _reload_handlers(tmp_path, monkeypatch, *, locale: str = "en", admin_usernames: str = ""):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LOCALE", locale)
    monkeypatch.setenv("ADMIN_USERNAMES", admin_usernames)
    config.get_settings.cache_clear()
    localization.get_localizer.cache_clear()

    import app.database as database
    import app.models as models
    import app.telebot.handlers as handlers

    database = importlib.reload(database)
    models = importlib.reload(models)
    handlers = importlib.reload(handlers)

    database.ensure_schema()
    return handlers, database, models


def test_start_creates_user_and_event_state(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=42, bot=bot)
    user = DummyUser(id=100, username="guest")
    update = DummyUpdate(user=user, chat=chat)
    context = DummyContext(bot)

    asyncio.run(handlers.start(update, context))

    assert chat.sent_messages or bot.sent_messages

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 100))
        event_state = session.scalar(select(models.EventState))

    assert db_user is not None
    assert event_state is not None


def test_application_flow_saves_profile_and_status(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, locale="ru")

    bot = DummyBot()
    chat = DummyChat(chat_id=7, bot=bot)
    user = DummyUser(id=77, username="guest")
    context = DummyContext(bot)

    update = DummyUpdate(user=user, chat=chat)
    result = asyncio.run(handlers.application_start(update, context))

    assert result == handlers.APPLICATION_FULL_NAME

    name_message = DummyMessage(text="Ada Lovelace")
    name_update = DummyUpdate(user=user, chat=chat, message=name_message)
    result = asyncio.run(handlers.application_full_name(name_update, context))
    assert result == handlers.APPLICATION_JOB

    job_message = DummyMessage(text="Engineer")
    job_update = DummyUpdate(user=user, chat=chat, message=job_message)
    result = asyncio.run(handlers.application_job(job_update, context))
    assert result == handlers.APPLICATION_CAREER

    career_message = DummyMessage(text="Backend")
    career_update = DummyUpdate(user=user, chat=chat, message=career_message)
    result = asyncio.run(handlers.application_career(career_update, context))

    assert result == handlers.ConversationHandler.END
    assert context.user_data == {}

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 77))

    assert db_user.full_name == "Ada Lovelace"
    assert db_user.job == "Engineer"
    assert db_user.career_path == "Backend"
    assert db_user.status == models.UserStatus.PROCESSING


def test_feedback_flow_records_submission(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=5, bot=bot)
    user = DummyUser(id=55, username="attendee")
    context = DummyContext(bot)

    with database.session_scope() as session:
        db_user = models.User(
            telegram_id=user.id,
            username=user.username,
            status=models.UserStatus.ATTENDEE,
            notifications_enabled=True,
        )
        session.add(db_user)
        state = models.EventState(event_started=True, current_event_id="event-1")
        session.add(state)

    update = DummyUpdate(user=user, chat=chat)
    result = asyncio.run(handlers.feedback_start(update, context))
    assert result == handlers.FEEDBACK_TEXT

    message = DummyMessage(text="Loved it!")
    feedback_update = DummyUpdate(user=user, chat=chat, message=message)
    result = asyncio.run(handlers.feedback_save(feedback_update, context))

    assert result == handlers.ConversationHandler.END

    with database.session_scope() as session:
        feedback = session.scalar(select(models.Feedback))

    assert feedback is not None
    assert feedback.feedback_text == "Loved it!"


def test_notifications_toggle_updates_flag(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=9, bot=bot)
    user = DummyUser(id=9, username="guest")
    context = DummyContext(bot)

    update = DummyUpdate(user=user, chat=chat)

    asyncio.run(handlers.notifications_disable(update, context))
    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 9))
        assert db_user.notifications_enabled is False

    asyncio.run(handlers.notifications_enable(update, context))
    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 9))
        assert db_user.notifications_enabled is True


def test_handle_admin_payload_saves_welcome_template(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=11, bot=bot)
    user = DummyUser(id=200, username="admin")
    message = DummyMessage(text="Welcome", chat_id=chat.id, message_id=99)
    update = DummyUpdate(user=user, chat=chat, message=message)
    context = DummyContext(bot)

    with database.session_scope() as session:
        handlers.set_admin_state(session, user.id, models.AdminStateType.WELCOME)

    asyncio.run(handlers.handle_admin_payload(update, context))

    with database.session_scope() as session:
        template = session.scalar(
            select(models.MessageTemplate).where(models.MessageTemplate.name == "welcome_message")
        )
        admin_state = session.scalar(select(models.AdminState))

    assert template is not None
    assert template.admin_chat_id == chat.id
    assert template.message_id == 99
    assert admin_state is None
    assert message.replies


def test_handle_admin_payload_upload_db_command_does_not_reply(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=17, bot=bot)
    user = DummyUser(id=201, username="admin")
    message = DummyMessage(text="/upload_database", chat_id=chat.id)
    update = DummyUpdate(user=user, chat=chat, message=message)
    context = DummyContext(bot)

    with database.session_scope() as session:
        handlers.set_admin_state(session, user.id, models.AdminStateType.UPLOAD_DB)

    asyncio.run(handlers.handle_admin_payload(update, context))

    assert message.replies == []


def test_process_upload_database_creates_users(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    csv_payload = (
        "user_id,username,full_name,job,career_path,status,notifications_enabled\n"
        "1,guest,Ada,Engineer,Backend,ATTENDEE,true\n"
        "2,admin,Admin,,,"  # admin should be skipped
        "NONE,true\n"
    ).encode("utf-8")
    bot.files["file-1"] = DummyFile(csv_payload)

    chat = DummyChat(chat_id=15, bot=bot)
    user = DummyUser(id=200, username="admin")
    message = DummyMessage(text=None, chat_id=chat.id)
    message.document = DummyDocument("file-1")
    update = DummyUpdate(user=user, chat=chat, message=message)
    context = DummyContext(bot)

    asyncio.run(handlers.process_upload_database(update, context, admin_id=user.id))

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 1))

    assert db_user is not None
    assert db_user.full_name == "Ada"
    assert db_user.status == models.UserStatus.ATTENDEE


def test_download_then_upload_database_updates_rows(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    with database.session_scope() as session:
        user_one = models.User(
            telegram_id=101,
            username="guest1",
            full_name="Ada",
            job="Engineer",
            career_path="Backend",
            status=models.UserStatus.PROCESSING,
            notifications_enabled=True,
        )
        user_two = models.User(
            telegram_id=202,
            username="guest2",
            full_name="Bob",
            job="Designer",
            career_path="UX",
            status=models.UserStatus.ATTENDEE,
            notifications_enabled=True,
        )
        session.add_all([user_one, user_two])
        session.add(
            models.Feedback(
                event_id="event-1",
                user=user_one,
                feedback_text="Great event!",
            )
        )

    bot = DummyBot()
    chat = DummyChat(chat_id=25, bot=bot)
    admin = DummyUser(id=999, username="admin")
    update = DummyUpdate(user=admin, chat=chat)
    context = DummyContext(bot)

    asyncio.run(handlers.download_database(update, context))

    assert len(chat.sent_documents) == 2
    users_payload, users_filename = chat.sent_documents[0]
    assert users_filename == "users.csv"

    users_csv = users_payload.decode("utf-8")
    reader = csv.DictReader(io.StringIO(users_csv))
    rows = list(reader)

    for row in rows:
        if row["user_id"] == "101":
            row["full_name"] = "Ada Lovelace"
            row["job"] = "Engineer II"
            row["career_path"] = "Platform"
            row["status"] = " waitlist "
            row["notifications_enabled"] = " false "
        if row["user_id"] == "202":
            row["status"] = "PROCESSING"

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    bot.files["file-upload"] = DummyFile(output.getvalue().encode("utf-8"))
    message = DummyMessage(text=None, chat_id=chat.id)
    message.document = DummyDocument("file-upload")
    upload_update = DummyUpdate(user=admin, chat=chat, message=message)

    asyncio.run(handlers.process_upload_database(upload_update, context, admin_id=admin.id))

    with database.session_scope() as session:
        refreshed_one = session.scalar(
            select(models.User).where(models.User.telegram_id == 101)
        )
        refreshed_two = session.scalar(
            select(models.User).where(models.User.telegram_id == 202)
        )

    assert refreshed_one.full_name == "Ada Lovelace"
    assert refreshed_one.job == "Engineer II"
    assert refreshed_one.career_path == "Platform"
    assert refreshed_one.status == models.UserStatus.WAITLIST
    assert refreshed_one.notifications_enabled is False
    assert refreshed_two.status == models.UserStatus.PROCESSING


def test_process_upload_database_accepts_semicolon_delimiter(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    with database.session_scope() as session:
        session.add(
            models.User(
                telegram_id=303,
                username="guest3",
                full_name="Carol",
                status=models.UserStatus.NONE,
                notifications_enabled=True,
            )
        )

    bot = DummyBot()
    csv_payload = (
        "user_id;username;full_name;job;career_path;status;notifications_enabled\n"
        "303;guest3;Carol Danvers;Pilot;Aviation;ATTENDEE;false\n"
    ).encode("utf-8")
    bot.files["file-2"] = DummyFile(csv_payload)

    chat = DummyChat(chat_id=18, bot=bot)
    user = DummyUser(id=202, username="admin")
    message = DummyMessage(text=None, chat_id=chat.id)
    message.document = DummyDocument("file-2")
    update = DummyUpdate(user=user, chat=chat, message=message)
    context = DummyContext(bot)

    asyncio.run(handlers.process_upload_database(update, context, admin_id=user.id))

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 303))

    assert db_user is not None
    assert db_user.full_name == "Carol Danvers"
    assert db_user.job == "Pilot"
    assert db_user.career_path == "Aviation"
    assert db_user.status == models.UserStatus.ATTENDEE
    assert db_user.notifications_enabled is False


def test_update_status_by_id_handles_invalid_and_success(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=21, bot=bot)
    user = DummyUser(id=99, username="admin")
    context = DummyContext(bot)

    message = DummyMessage(text="/approve_id", chat_id=chat.id)
    update = DummyUpdate(user=user, chat=chat, message=message)
    asyncio.run(handlers.update_status_by_id(update, context, models.UserStatus.ATTENDEE))

    message = DummyMessage(text="/approve_id abc", chat_id=chat.id)
    update = DummyUpdate(user=user, chat=chat, message=message)
    asyncio.run(handlers.update_status_by_id(update, context, models.UserStatus.ATTENDEE))

    with database.session_scope() as session:
        session.add(
            models.User(
                telegram_id=123,
                username="member",
                status=models.UserStatus.NONE,
                notifications_enabled=True,
            )
        )

    created: list[object] = []

    def fake_create_task(coro):
        created.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(handlers.asyncio, "create_task", fake_create_task)

    message = DummyMessage(text="/approve_id 123", chat_id=chat.id)
    update = DummyUpdate(user=user, chat=chat, message=message)
    asyncio.run(handlers.update_status_by_id(update, context, models.UserStatus.ATTENDEE))

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 123))

    assert db_user.status == models.UserStatus.ATTENDEE
    assert created


def test_event_start_and_cancel(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=33, bot=bot)
    user = DummyUser(id=88, username="admin")
    context = DummyContext(bot)

    update = DummyUpdate(user=user, chat=chat)

    broadcasted: list[str] = []

    async def fake_broadcast_text(bot_obj, text: str) -> None:
        broadcasted.append(text)

    monkeypatch.setattr(handlers, "broadcast_text", fake_broadcast_text)

    asyncio.run(handlers.event_start(update, context))

    with database.session_scope() as session:
        state = session.scalar(select(models.EventState))

    assert state.event_started is True
    assert broadcasted

    asyncio.run(handlers.event_cancel(update, context))
    with database.session_scope() as session:
        state = session.scalar(select(models.EventState))

    assert state.event_started is False
