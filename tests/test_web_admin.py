import importlib
from datetime import datetime, timezone

import app.config as config
from fastapi.testclient import TestClient
from sqlalchemy import select


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


def _setup_admin_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    config_file = tmp_path / "config.yaml"
    config_file.write_text("telegram_token: token\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(config_file))
    config.get_settings.cache_clear()

    import app.database as database
    import app.models as models
    import app.services.admin as admin_service
    import app.services.events as events
    import app.services.messaging as messaging
    import app.services.posts as posts
    import app.services.registrations as registrations
    import app.web.admin as admin

    database = importlib.reload(database)
    models = importlib.reload(models)
    admin_service = importlib.reload(admin_service)
    events = importlib.reload(events)
    messaging = importlib.reload(messaging)
    posts = importlib.reload(posts)
    registrations = importlib.reload(registrations)
    admin = importlib.reload(admin)
    database.ensure_schema()

    settings = config.Settings(
        telegram_token="token",
        basic_auth_username="admin",
        basic_auth_password="secret",
        event_name="Demo",
        attendee_limit=1,
        timezone="UTC",
    )
    app = admin.create_app(settings, bot=DummyBot())
    client = TestClient(app)
    return client, database, models, settings


def test_admin_requires_auth(tmp_path, monkeypatch):
    client, _, _, _ = _setup_admin_app(tmp_path, monkeypatch)

    response = client.get("/admin")

    assert response.status_code == 401


def test_dashboard_and_posts_flow(tmp_path, monkeypatch):
    client, database, models, settings = _setup_admin_app(tmp_path, monkeypatch)

    with database.session_scope() as session:
        event = models.Event(name=settings.event_name, capacity=2)
        session.add(event)
        session.flush()
        user = models.User(telegram_id=None, username=None, display_name="Guest")
        session.add(user)
        session.flush()
        session.add(
            models.Registration(
                event_id=event.id,
                user_id=user.id,
                category=models.RegistrationCategory.ATTENDEE,
                status=models.RegistrationStatus.PENDING,
            )
        )
        session.add(
            models.ScheduledPost(
                title="Hello",
                content="Body",
                send_at=datetime.now(timezone.utc),
            )
        )

    response = client.get("/admin", auth=("admin", "secret"))
    assert response.status_code == 200
    assert "Timezone: UTC" in response.text

    response = client.get("/admin/posts", auth=("admin", "secret"))
    assert response.status_code == 200


def test_create_and_cancel_post(tmp_path, monkeypatch):
    client, database, models, _ = _setup_admin_app(tmp_path, monkeypatch)

    response = client.post(
        "/admin/posts",
        auth=("admin", "secret"),
        data={"title": "Hi", "content": "There", "send_at": "2025-01-01 10:00"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with database.session_scope() as session:
        post = session.scalar(select(models.ScheduledPost))
        assert post is not None
        post_id = post.id

    response = client.post(
        f"/admin/posts/{post_id}/cancel",
        auth=("admin", "secret"),
        follow_redirects=False,
    )
    assert response.status_code == 303

    with database.session_scope() as session:
        assert session.get(models.ScheduledPost, post_id) is None


def test_create_post_invalid_date(tmp_path, monkeypatch):
    client, _, _, _ = _setup_admin_app(tmp_path, monkeypatch)

    response = client.post(
        "/admin/posts",
        auth=("admin", "secret"),
        data={"title": "Hi", "content": "There", "send_at": "bad-date"},
    )

    assert response.status_code == 400


def test_update_timezone_and_limit(tmp_path, monkeypatch):
    client, database, models, settings = _setup_admin_app(tmp_path, monkeypatch)

    response = client.post(
        "/admin/settings/timezone",
        auth=("admin", "secret"),
        data={"timezone_value": " "},
    )
    assert response.status_code == 400

    response = client.post(
        "/admin/settings/timezone",
        auth=("admin", "secret"),
        data={"timezone_value": "Not/AZone"},
    )
    assert response.status_code == 400

    response = client.post(
        "/admin/settings/timezone",
        auth=("admin", "secret"),
        data={"timezone_value": "UTC", "return_to": "/admin/posts"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/posts")

    with database.session_scope() as session:
        event = models.Event(name=settings.event_name, capacity=1)
        session.add(event)
        session.flush()

    response = client.post(
        "/admin/event/limit",
        auth=("admin", "secret"),
        data={"limit": " "},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post(
        "/admin/event/limit",
        auth=("admin", "secret"),
        data={"limit": "2"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_registration_updates_and_manual_entry(tmp_path, monkeypatch):
    client, database, models, settings = _setup_admin_app(tmp_path, monkeypatch)

    with database.session_scope() as session:
        event = models.Event(name=settings.event_name, capacity=1)
        session.add(event)
        session.flush()

        user = models.User(telegram_id=123, username="user", notifications_enabled=True)
        session.add(user)
        session.flush()
        registration = models.Registration(
            event_id=event.id,
            user_id=user.id,
            category=models.RegistrationCategory.ATTENDEE,
            status=models.RegistrationStatus.PENDING,
        )
        session.add(registration)
        session.flush()

        approved_user = models.User(telegram_id=456, username="approved")
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
        reg_id = registration.id

    response = client.post(
        f"/admin/registrations/{reg_id}/status",
        auth=("admin", "secret"),
        data={"status_value": "approved"},
    )
    assert response.status_code == 409

    response = client.post(
        f"/admin/registrations/{reg_id}/status",
        auth=("admin", "secret"),
        data={"status_value": "waitlisted", "return_to": "/admin/registrations"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post(
        f"/admin/registrations/{reg_id}/delete",
        auth=("admin", "secret"),
        data={"return_to": "/admin/registrations"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post(
        "/admin/registrations/manual",
        auth=("admin", "secret"),
        data={
            "display_name": "Manual",
            "contact": "manual@example.com",
            "category_value": "lecturer",
            "notes": "VIP",
            "priority": "true",
            "return_to": "/admin/registrations",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with database.session_scope() as session:
        manual = session.scalar(select(models.User).where(models.User.display_name == "Manual"))
        assert manual is not None
        assert manual.is_manual is True
