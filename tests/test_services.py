import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from telegram.error import TelegramError

import app.config as config


def _reload_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    config.get_settings.cache_clear()

    import app.database as database
    import app.models as models

    database = importlib.reload(database)
    models = importlib.reload(models)
    database.ensure_schema()
    import app.services.admin as admin_service
    import app.services.events as events
    import app.services.messaging as messaging
    import app.services.posts as posts
    import app.services.registrations as registrations
    import app.services.users as users

    admin_service = importlib.reload(admin_service)
    events = importlib.reload(events)
    messaging = importlib.reload(messaging)
    posts = importlib.reload(posts)
    registrations = importlib.reload(registrations)
    users = importlib.reload(users)

    return (
        database,
        models,
        admin_service,
        events,
        messaging,
        posts,
        registrations,
        users,
    )


def test_get_or_create_default_event_updates_capacity(tmp_path, monkeypatch):
    database, models, _, events, _, _, _, _ = _reload_database(tmp_path, monkeypatch)
    settings = config.Settings(
        telegram_token="token", event_name="Demo", attendee_limit=10
    )

    with database.session_scope() as session:
        event = events.get_or_create_default_event(session, settings)
        assert event.name == "Demo"
        assert event.capacity == 10

    with database.session_scope() as session:
        event = session.scalar(select(models.Event).where(models.Event.name == "Demo"))
        event.capacity = None
        session.flush()

    settings = config.Settings(telegram_token="token", event_name="Demo", attendee_limit=5)
    with database.session_scope() as session:
        updated = events.get_or_create_default_event(session, settings)
        assert updated.capacity == 5


def test_create_manual_attendee_creates_user_and_registration(tmp_path, monkeypatch):
    database, models, admin_service, events, _, _, _, _ = _reload_database(
        tmp_path, monkeypatch
    )
    settings = config.Settings(
        telegram_token="token", event_name="Demo", attendee_limit=None
    )

    with database.session_scope() as session:
        event = events.get_or_create_default_event(session, settings)
        registration = admin_service.create_manual_attendee(
            session,
            event=event,
            display_name="Manual User",
            contact="manual@example.com",
            category=models.RegistrationCategory.LECTURER,
            notes="VIP",
        )
        assert registration.user.display_name == "Manual User"
        assert registration.user.is_manual is True
        assert registration.category == models.RegistrationCategory.LECTURER


def test_register_user_waitlists_when_capacity_full(tmp_path, monkeypatch):
    database, models, _, events, _, _, registrations, _ = _reload_database(
        tmp_path, monkeypatch
    )
    settings = config.Settings(
        telegram_token="token", event_name="Demo", attendee_limit=1
    )

    with database.session_scope() as session:
        event = events.get_or_create_default_event(session, settings)
        existing_user = models.User(telegram_id=1, username="first")
        session.add(existing_user)
        session.flush()
        session.add(
            models.Registration(
                event_id=event.id,
                user_id=existing_user.id,
                category=models.RegistrationCategory.ATTENDEE,
                status=models.RegistrationStatus.APPROVED,
            )
        )

        new_user = models.User(telegram_id=2, username="second")
        session.add(new_user)
        session.flush()

        result = registrations.register_user(
            session,
            event=event,
            user=new_user,
            category=models.RegistrationCategory.ATTENDEE,
            notes="Note",
        )

    assert result.created is True
    assert result.waitlisted is True
    assert result.registration.status == models.RegistrationStatus.WAITLISTED


def test_register_user_updates_existing_registration(tmp_path, monkeypatch):
    database, models, _, events, _, _, registrations, _ = _reload_database(
        tmp_path, monkeypatch
    )
    settings = config.Settings(telegram_token="token", event_name="Demo")

    with database.session_scope() as session:
        event = events.get_or_create_default_event(session, settings)
        user = models.User(telegram_id=3, username="repeat")
        session.add(user)
        session.flush()
        registration = models.Registration(
            event_id=event.id,
            user_id=user.id,
            category=models.RegistrationCategory.LECTURER,
            status=models.RegistrationStatus.PENDING,
            notes="Old",
        )
        session.add(registration)
        session.flush()

        result = registrations.register_user(
            session,
            event=event,
            user=user,
            category=models.RegistrationCategory.LECTURER,
            notes="Updated",
        )

    assert result.created is False
    assert result.waitlisted is False
    assert result.registration.notes == "Updated"


def test_update_registration_status_enforces_capacity(tmp_path, monkeypatch):
    database, models, _, events, _, _, registrations, _ = _reload_database(
        tmp_path, monkeypatch
    )
    settings = config.Settings(
        telegram_token="token", event_name="Demo", attendee_limit=1
    )

    with database.session_scope() as session:
        event = events.get_or_create_default_event(session, settings)
        approved_user = models.User(telegram_id=10, username="approved")
        session.add(approved_user)
        session.flush()
        session.add(
            models.Registration(
                event_id=event.id,
                user_id=approved_user.id,
                category=models.RegistrationCategory.ATTENDEE,
                status=models.RegistrationStatus.APPROVED,
            )
        )

        pending_user = models.User(telegram_id=11, username="pending")
        session.add(pending_user)
        session.flush()
        pending = models.Registration(
            event_id=event.id,
            user_id=pending_user.id,
            category=models.RegistrationCategory.ATTENDEE,
            status=models.RegistrationStatus.PENDING,
        )
        session.add(pending)
        session.flush()

        with pytest.raises(registrations.CapacityError):
            registrations.update_registration_status(
                session,
                pending,
                status=models.RegistrationStatus.APPROVED,
                is_priority=False,
            )


def test_get_or_create_user_refreshes_profile(tmp_path, monkeypatch):
    database, models, _, _, _, _, _, users = _reload_database(tmp_path, monkeypatch)
    tg_user = SimpleNamespace(
        id=42,
        username="alice",
        first_name="Alice",
        last_name="Doe",
        full_name="Alice Doe",
    )

    with database.session_scope() as session:
        user = users.get_or_create_user(session, tg_user)
        assert user.display_name == "Alice Doe"

    tg_user.username = "alice_new"
    tg_user.full_name = ""
    tg_user.first_name = "Alicia"
    tg_user.last_name = "Doe"

    with database.session_scope() as session:
        refreshed = users.get_or_create_user(session, tg_user)
        assert refreshed.username == "alice_new"
        assert refreshed.display_name == "alice_new"
        assert refreshed.contact == "alice_new"


def test_schedule_and_query_posts(tmp_path, monkeypatch):
    database, models, _, _, _, posts, _, _ = _reload_database(tmp_path, monkeypatch)
    send_at = datetime(2024, 1, 1, 12, 0, 0)

    with database.session_scope() as session:
        post = posts.schedule_post(
            session, title="Title", content="Body", send_at=send_at
        )
        assert post.send_at.tzinfo is timezone.utc

    with database.session_scope() as session:
        pending = posts.get_pending_posts(
            session, now=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        )
        assert [item.id for item in pending] == [post.id]


def test_broadcast_message_and_post(tmp_path, monkeypatch):
    database, models, _, _, messaging, posts, _, _ = _reload_database(
        tmp_path, monkeypatch
    )

    class DummyBot:
        def __init__(self) -> None:
            self.sent = []

        async def send_message(self, chat_id: int, text: str) -> None:
            if chat_id == 2:
                raise TelegramError("boom")
            self.sent.append((chat_id, text))

    with database.session_scope() as session:
        session.add(
            models.User(telegram_id=1, username="ok", notifications_enabled=True)
        )
        session.add(
            models.User(telegram_id=2, username="fail", notifications_enabled=True)
        )
        session.add(
            models.User(telegram_id=None, username="skip", notifications_enabled=True)
        )
        session.flush()

        delivered = asyncio.run(messaging.broadcast_message(session, DummyBot(), "Hi"))
        assert delivered == 1

        post = models.ScheduledPost(
            title="Title",
            content="Body",
            send_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(post)
        session.flush()

        asyncio.run(posts.broadcast_post(session, DummyBot(), post))
        assert post.sent_at is not None
