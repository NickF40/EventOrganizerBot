import asyncio
import csv
import importlib
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import app.config as config
import app.localization as localization
from sqlalchemy import select
from telegram.ext import Application


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
        refreshed_one = session.scalar(select(models.User).where(models.User.telegram_id == 101))
        refreshed_two = session.scalar(select(models.User).where(models.User.telegram_id == 202))

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


def test_broadcast_text_sends_to_users(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()

    with database.session_scope() as session:
        session.add_all(
            [
                models.User(
                    telegram_id=201,
                    username="alpha",
                    status=models.UserStatus.ATTENDEE,
                    notifications_enabled=True,
                ),
                models.User(
                    telegram_id=202,
                    username="beta",
                    status=models.UserStatus.PROCESSING,
                    notifications_enabled=True,
                ),
            ]
        )
        session.add(models.EventState(event_started=True, current_event_id="event-1"))

    asyncio.run(handlers.broadcast_text(bot, "Broadcast!"))

    assert len(bot.sent_messages) == 2
    assert {message["chat_id"] for message in bot.sent_messages} == {201, 202}


def test_show_status_and_notifications(tmp_path, monkeypatch):
    handlers, _database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=18, bot=bot)
    user = DummyUser(id=18, username="guest")
    context = DummyContext(bot)

    update = DummyUpdate(user=user, chat=chat)

    asyncio.run(handlers.show_status(update, context))
    asyncio.run(handlers.show_notifications(update, context))

    assert bot.sent_messages[0]["text"] == handlers.status_text(models.UserStatus.NONE)
    assert bot.sent_messages[1]["text"] == handlers.notifications_text(True)


def test_notifications_toggle_and_cancel_application(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=19, bot=bot)
    user = DummyUser(id=19, username="guest")
    context = DummyContext(bot)
    update = DummyUpdate(user=user, chat=chat)

    asyncio.run(handlers.notifications_disable(update, context))
    asyncio.run(handlers.notifications_enable(update, context))

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 19))

    assert db_user.notifications_enabled is True

    with database.session_scope() as session:
        session.add(
            models.User(
                telegram_id=204,
                username="applicant",
                status=models.UserStatus.PROCESSING,
                full_name="Ada",
                job="Engineer",
                career_path="Backend",
                friend_usernames="buddy",
                notifications_enabled=True,
            )
        )

    applicant = DummyUser(id=204, username="applicant")
    applicant_chat = DummyChat(chat_id=204, bot=bot)
    applicant_update = DummyUpdate(user=applicant, chat=applicant_chat)
    asyncio.run(handlers.cancel_application(applicant_update, context))

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 204))

    assert db_user.status == models.UserStatus.NONE
    assert db_user.full_name is None
    assert db_user.friend_usernames is None


def test_schedule_and_feedback_exit_paths(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=22, bot=bot)
    user = DummyUser(id=22, username="guest")
    context = DummyContext(bot)

    with database.session_scope() as session:
        session.add(
            models.MessageTemplate(
                name="schedule_message",
                admin_chat_id=55,
                message_id=77,
            )
        )

    update = DummyUpdate(user=user, chat=chat)
    asyncio.run(handlers.schedule(update, context))
    assert bot.copied_messages == [{"chat_id": 22, "from_chat_id": 55, "message_id": 77}]

    feedback_result = asyncio.run(handlers.feedback_start(update, context))
    assert feedback_result == handlers.ConversationHandler.END


def test_admin_schedule_template_and_broadcasts(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=30, bot=bot)
    user = DummyUser(id=30, username="admin")
    context = DummyContext(bot)

    with database.session_scope() as session:
        session.add(
            models.AdminState(
                admin_id=user.id,
                waiting_for=models.AdminStateType.SCHEDULE,
                ttl_seconds=300,
            )
        )

    message = DummyMessage(text="Schedule update", chat_id=chat.id, message_id=99)
    update = DummyUpdate(user=user, chat=chat, message=message)
    asyncio.run(handlers.handle_admin_payload(update, context))

    with database.session_scope() as session:
        template = session.scalar(
            select(models.MessageTemplate).where(models.MessageTemplate.name == "schedule_message")
        )

    assert template is not None
    assert template.message_id == 99

    with database.session_scope() as session:
        session.add_all(
            [
                models.User(
                    telegram_id=301,
                    username="attendee",
                    status=models.UserStatus.ATTENDEE,
                    notifications_enabled=True,
                ),
                models.User(
                    telegram_id=302,
                    username="silent",
                    status=models.UserStatus.ATTENDEE,
                    notifications_enabled=False,
                ),
                models.User(
                    telegram_id=303,
                    username="waiter",
                    status=models.UserStatus.WAITLIST,
                    notifications_enabled=True,
                ),
            ]
        )

    payload = DummyMessage(text="Broadcast", chat_id=chat.id, message_id=101)
    with database.session_scope() as session:
        asyncio.run(
            handlers.broadcast_payload(
                session,
                context,
                payload,
                models.AdminStateType.BROADCAST_ATTENDEE,
            )
        )

    assert bot.copied_messages == [{"chat_id": 301, "from_chat_id": 30, "message_id": 101}]


def test_download_database_and_unknown_command(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=40, bot=bot)
    user = DummyUser(id=40, username="admin")
    context = DummyContext(bot)

    with database.session_scope() as session:
        db_user = models.User(
            telegram_id=401,
            username="member",
            status=models.UserStatus.ATTENDEE,
            notifications_enabled=True,
        )
        session.add(db_user)
        session.add(models.EventState(event_started=True, current_event_id="event-1"))
        session.flush()
        session.add(
            models.Feedback(
                event_id="event-1",
                user_id=db_user.id,
                feedback_text="Nice",
                created_at=datetime.utcnow(),
            )
        )

    update = DummyUpdate(user=user, chat=chat)
    asyncio.run(handlers.download_database(update, context))

    assert len(chat.sent_documents) == 2
    assert chat.sent_documents[0][1] == "users.csv"
    assert chat.sent_documents[1][1] == "feedback.csv"

    non_admin = DummyUser(id=41, username="guest")
    non_admin_chat = DummyChat(chat_id=41, bot=bot)
    non_admin_update = DummyUpdate(user=non_admin, chat=non_admin_chat)
    asyncio.run(handlers.unknown_command(non_admin_update, context))

    assert non_admin_chat.sent_messages


def test_friend_helpers_and_application_restart(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    assert handlers.normalize_friend_username("@Guest") == "guest"
    assert handlers.parse_friend_usernames("@Guest, @Guest friend") == ["guest", "friend"]
    assert handlers.serialize_friend_usernames(["guest", "friend"]) == "guest,friend"
    assert handlers.deserialize_friend_usernames("guest, friend") == {"guest", "friend"}

    bot = DummyBot()
    chat = DummyChat(chat_id=50, bot=bot)
    user = DummyUser(id=50, username="guest")
    context = DummyContext(bot)

    with database.session_scope() as session:
        session.add(
            models.User(
                telegram_id=50,
                username="guest",
                status=models.UserStatus.PROCESSING,
                notifications_enabled=True,
            )
        )

    update = DummyUpdate(user=user, chat=chat)
    result = asyncio.run(handlers.application_start(update, context))
    assert result == handlers.ConversationHandler.END


def test_application_friends_persists_data(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=52, bot=bot)
    user = DummyUser(id=52, username="guest")
    context = DummyContext(bot)
    context.user_data.update({"full_name": "Ada", "job": "Engineer", "career_path": "Backend"})

    message = DummyMessage(text="@FriendOne friendtwo")
    update = DummyUpdate(user=user, chat=chat, message=message)
    result = asyncio.run(handlers.application_friends(update, context))
    assert result == handlers.ConversationHandler.END

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.telegram_id == 52))

    assert db_user.friend_usernames == "friendone,friendtwo"
    assert db_user.status == models.UserStatus.PROCESSING


def test_template_helpers_and_attendee_notification(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch)

    bot = DummyBot()
    chat = DummyChat(chat_id=60, bot=bot)
    user = DummyUser(id=60, username="guest")
    context = DummyContext(bot)
    update = DummyUpdate(user=user, chat=chat)

    asyncio.run(handlers.send_welcome_message(update, context, None))
    asyncio.run(handlers.send_schedule_message(update, context, None))

    localizer = handlers.get_bot_localizer()
    assert chat.sent_messages[0][0] == localizer.get("bot.templates.missing_welcome")
    assert chat.sent_messages[1][0] == localizer.get("bot.templates.missing_schedule")

    with database.session_scope() as session:
        session.add(
            models.User(
                telegram_id=601,
                username="attendee",
                status=models.UserStatus.ATTENDEE,
                notifications_enabled=True,
            )
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(handlers.asyncio, "sleep", fake_sleep)
    asyncio.run(handlers.send_attendee_notification(bot, 601))
    assert bot.sent_messages


def test_admin_commands_and_event_id(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=70, bot=bot)
    user = DummyUser(id=70, username="admin")
    context = DummyContext(bot)

    update = DummyUpdate(user=user, chat=chat, message=DummyMessage(text="/set_event_id"))
    asyncio.run(handlers.set_event_id(update, context))
    assert chat.sent_messages

    update = DummyUpdate(user=user, chat=chat, message=DummyMessage(text="/set_event_id event-2"))
    asyncio.run(handlers.set_event_id(update, context))

    with database.session_scope() as session:
        state = session.scalar(select(models.EventState))

    assert state.current_event_id == "event-2"

    asyncio.run(handlers.admin_help(update, context))
    asyncio.run(handlers.set_welcome_message(update, context))
    asyncio.run(handlers.set_schedule_message(update, context))
    asyncio.run(handlers.urgent_notification(update, context))
    asyncio.run(handlers.urgent_notification_attendee(update, context))
    asyncio.run(handlers.upload_database(update, context))

    with database.session_scope() as session:
        admin_state = session.scalar(select(models.AdminState))

    assert admin_state is not None


def test_status_updates_and_admin_guard(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=80, bot=bot)
    user = DummyUser(id=80, username="admin")
    context = DummyContext(bot)

    with database.session_scope() as session:
        session.add(
            models.User(
                telegram_id=801,
                username="member",
                status=models.UserStatus.PROCESSING,
                notifications_enabled=True,
            )
        )
        session.add(
            models.User(
                telegram_id=802,
                username="empty",
                status=models.UserStatus.NONE,
                notifications_enabled=True,
            )
        )

    update = DummyUpdate(user=user, chat=chat, message=DummyMessage(text="/approve missing"))
    asyncio.run(handlers.update_status_by_username(update, context, models.UserStatus.ATTENDEE))

    update = DummyUpdate(user=user, chat=chat, message=DummyMessage(text="/approve empty"))
    asyncio.run(handlers.update_status_by_username(update, context, models.UserStatus.ATTENDEE))

    update = DummyUpdate(user=user, chat=chat, message=DummyMessage(text="/approve member"))
    asyncio.run(handlers.update_status_by_username(update, context, models.UserStatus.ATTENDEE))

    with database.session_scope() as session:
        db_user = session.scalar(select(models.User).where(models.User.username == "member"))

    assert db_user.status == models.UserStatus.ATTENDEE

    non_admin = DummyUser(id=81, username="guest")
    non_admin_chat = DummyChat(chat_id=81, bot=bot)
    non_admin_update = DummyUpdate(user=non_admin, chat=non_admin_chat)

    created: list[object] = []

    def fake_create_task(coro):
        created.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(handlers.asyncio, "create_task", fake_create_task)

    assert handlers.ensure_admin(non_admin_update) is False
    assert created


def test_check_applications_summary(tmp_path, monkeypatch):
    handlers, database, models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=90, bot=bot)
    user = DummyUser(id=90, username="admin")
    context = DummyContext(bot)

    with database.session_scope() as session:
        session.add_all(
            [
                models.User(
                    telegram_id=901,
                    username="applicant",
                    status=models.UserStatus.PROCESSING,
                    notifications_enabled=True,
                ),
                models.User(
                    telegram_id=902,
                    username="waiter",
                    status=models.UserStatus.WAITLIST,
                    notifications_enabled=True,
                ),
            ]
        )

    update = DummyUpdate(user=user, chat=chat)
    asyncio.run(handlers.check_applications(update, context))

    assert chat.sent_messages


def test_friend_helpers_empty_and_template_success(tmp_path, monkeypatch):
    handlers, _database, models = _reload_handlers(tmp_path, monkeypatch)

    assert handlers.parse_friend_usernames("") == []
    assert handlers.serialize_friend_usernames([]) is None
    assert handlers.deserialize_friend_usernames(None) == set()

    bot = DummyBot()
    chat = DummyChat(chat_id=95, bot=bot)
    user = DummyUser(id=95, username="guest")
    context = DummyContext(bot)
    update = DummyUpdate(user=user, chat=chat)

    welcome = models.MessageTemplate(name="welcome_message", admin_chat_id=11, message_id=22)
    schedule = models.MessageTemplate(name="schedule_message", admin_chat_id=11, message_id=33)

    asyncio.run(handlers.send_welcome_message(update, context, welcome))
    asyncio.run(handlers.send_schedule_message(update, context, schedule))

    assert bot.copied_messages == [
        {"chat_id": 95, "from_chat_id": 11, "message_id": 22},
        {"chat_id": 95, "from_chat_id": 11, "message_id": 33},
    ]


def test_check_applications_guard_paths(tmp_path, monkeypatch):
    handlers, _database, _models = _reload_handlers(tmp_path, monkeypatch, admin_usernames="admin")

    bot = DummyBot()
    chat = DummyChat(chat_id=96, bot=bot)
    non_admin = DummyUser(id=96, username="guest")
    update = DummyUpdate(user=non_admin, chat=chat)

    asyncio.run(handlers.check_applications(update, DummyContext(bot)))

    admin = DummyUser(id=97, username="admin")
    update = DummyUpdate(user=admin, chat=None)
    asyncio.run(handlers.check_applications(update, DummyContext(bot)))


def test_register_adds_handlers(tmp_path, monkeypatch):
    handlers, _database, _models = _reload_handlers(tmp_path, monkeypatch)

    application = Application.builder().token("token").build()
    handlers.register(application)

    total_handlers = sum(len(group) for group in application.handlers.values())
    assert total_handlers > 0
