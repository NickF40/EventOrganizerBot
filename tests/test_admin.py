import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import sys
import types

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_TOKEN", "dummy-token")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "secret")

sys.modules.setdefault("telebot", types.SimpleNamespace(Bot=object))
sys.modules.setdefault("telebot.error", types.SimpleNamespace(TelegramError=Exception))

import app.config as app_config
from app.config import Settings
from app.database import get_session
from app.localization import get_localizer
from app.models import RegistrationCategory, RegistrationStatus
from app.web import admin as admin_module


def _make_scalar_result(values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _override_session(session):
    def dependency():
        yield session

    return dependency


@pytest.fixture
def settings():
    return Settings(
        telegram_token="token",
        admin_ids=[],
        basic_auth_username="admin",
        basic_auth_password="secret",
    )


@pytest.fixture
def admin_client(settings):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    app = admin_module.create_app(settings, bot=bot)
    client = TestClient(app)
    try:
        yield client, app, bot
    finally:
        client.close()


def test_index_redirects_to_dashboard(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.get("/", auth=("admin", "secret"), follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin"


def test_posts_page_renders_lists(admin_client):
    client, app, _ = admin_client
    upcoming_post = SimpleNamespace(
        id=1,
        title="Future",
        content="Soon",
        send_at=datetime.now(timezone.utc),
    )
    sent_post = SimpleNamespace(
        id=2,
        title="Past",
        content="Earlier",
        sent_at=datetime.now(timezone.utc),
    )

    session = MagicMock()
    session.execute.side_effect = [
        _make_scalar_result([upcoming_post]),
        _make_scalar_result([sent_post]),
    ]

    app.dependency_overrides[get_session] = _override_session(session)

    response = client.get("/admin/posts", auth=("admin", "secret"))

    assert response.status_code == 200
    assert "Future" in response.text
    assert "Past" in response.text
    assert "Timezone preferences" in response.text
    assert "Europe/Moscow" in response.text
    assert "Asia/Almaty" in response.text


def test_registrations_page_includes_limit_form(admin_client, monkeypatch):
    client, app, _ = admin_client
    session = MagicMock()
    session.execute.return_value = _make_scalar_result([])
    session.scalar.return_value = 0
    app.dependency_overrides[get_session] = _override_session(session)

    event = SimpleNamespace(id=1, capacity=50)
    monkeypatch.setattr(admin_module, "get_or_create_default_event", lambda *args, **kwargs: event)

    response = client.get("/admin/registrations", auth=("admin", "secret"))

    assert response.status_code == 200
    assert "Attendee limit" in response.text
    assert "Leave blank to remove the limit." in response.text
    assert 'value="50"' in response.text


def test_dashboard_renders_summary(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    event = SimpleNamespace(id=1, name="Community Event", capacity=100)
    session.scalar.side_effect = [event, 2]
    session.execute.side_effect = [
        MagicMock(
            all=MagicMock(
                return_value=[
                    (RegistrationStatus.APPROVED, 5),
                    (RegistrationStatus.WAITLISTED, 1),
                ]
            )
        ),
        MagicMock(
            all=MagicMock(
                return_value=[
                    (RegistrationCategory.ATTENDEE, 10),
                    (RegistrationCategory.LECTURER, 3),
                ]
            )
        ),
        _make_scalar_result([]),
    ]

    app.dependency_overrides[get_session] = _override_session(session)

    response = client.get("/admin", auth=("admin", "secret"))

    assert response.status_code == 200
    assert "Event overview" in response.text
    assert "Upcoming posts" in response.text


def test_create_post_schedules(admin_client, monkeypatch):
    client, app, _ = admin_client
    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    schedule_post = MagicMock()
    monkeypatch.setattr(admin_module, "schedule_post", schedule_post)

    response = client.post(
        "/admin/posts",
        data={
            "title": "Hello",
            "content": "World",
            "send_at": "2024-01-01T12:00",
        },
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/posts")
    schedule_post.assert_called_once()
    _, kwargs = schedule_post.call_args
    assert kwargs["title"] == "Hello"
    assert kwargs["content"] == "World"
    assert kwargs["send_at"].tzinfo is not None


def test_update_limit_removes_capacity(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    event = SimpleNamespace(capacity=123)
    session.scalar.return_value = event
    session.flush = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.post(
        "/admin/event/limit",
        data={"limit": "   "},
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Attendee%20limit%20removed" in response.headers["location"]
    assert event.capacity is None
    assert app.state.settings.attendee_limit is None
    session.flush.assert_called_once()


def test_update_limit_sets_new_capacity(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    event = SimpleNamespace(capacity=None)
    session.scalar.return_value = event
    session.flush = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.post(
        "/admin/event/limit",
        data={"limit": "25"},
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Attendee%20limit%20updated" in response.headers["location"]
    assert event.capacity == 25
    assert app.state.settings.attendee_limit == 25
    session.flush.assert_called_once()


def test_update_limit_requires_integer(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    event = SimpleNamespace(capacity=None)
    session.scalar.return_value = event
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.post(
        "/admin/event/limit",
        data={"limit": "many"},
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Limit must be an integer"


def test_update_limit_disallows_negative_values(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    event = SimpleNamespace(capacity=None)
    session.scalar.return_value = event
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.post(
        "/admin/event/limit",
        data={"limit": "-1"},
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Limit must be zero or greater"


def test_update_timezone_changes_setting(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.post(
        "/admin/settings/timezone",
        data={
            "timezone_value": "Europe/Paris",
            "return_to": "/admin/posts",
        },
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/posts")
    assert app.state.settings.timezone == "Europe/Paris"


def test_update_timezone_rejects_invalid(admin_client):
    client, app, _ = admin_client

    response = client.post(
        "/admin/settings/timezone",
        data={"timezone_value": "Mars/Phobos"},
        auth=("admin", "secret"),
    )

    assert response.status_code == 400


def test_update_timezone_handles_read_only(admin_client, monkeypatch, tmp_path):
    client, app, _ = admin_client
    config_file = tmp_path / "config.yaml"
    config_file.write_text("timezone: UTC\n", encoding="utf-8")
    config_file.chmod(0o400)

    monkeypatch.setattr(app_config, "config_path", lambda: config_file)

    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.post(
        "/admin/settings/timezone",
        data={
            "timezone_value": "Europe/Moscow",
            "return_to": "/admin/posts",
        },
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    config_file.chmod(0o600)

    assert response.status_code == 303
    redirect = urlparse(response.headers["location"])
    params = parse_qs(redirect.query)
    assert "msg" in params
    assert "(not saved to disk)" in params["msg"][0]
    assert app.state.settings.timezone == "Europe/Moscow"


def test_send_urgent_broadcasts_message(admin_client, monkeypatch):
    client, app, bot = admin_client
    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    broadcast = AsyncMock(return_value=5)
    monkeypatch.setattr(admin_module, "broadcast_message", broadcast)

    response = client.post(
        "/admin/urgent",
        data={"message": "Alert!"},
        auth=("admin", "secret"),
    )

    assert response.status_code == 200
    broadcast.assert_awaited_once_with(session, bot, "Alert!")
    assert "Alert!" in response.text


def test_update_status_updates_registration(admin_client, monkeypatch):
    client, app, bot = admin_client
    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    registration = SimpleNamespace(user=SimpleNamespace(telegram_id=123), is_priority=True)
    session.get.return_value = registration

    update_status = MagicMock()
    monkeypatch.setattr(admin_module, "update_registration_status", update_status)

    create_task = MagicMock()
    monkeypatch.setattr(admin_module.asyncio, "create_task", create_task)

    response = client.post(
        "/admin/registrations/1/status",
        data={
            "status_value": "approved",
            "priority_value": "true",
            "return_to": "/admin/registrations/approved",
        },
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/registrations/approved"
    update_status.assert_called_once()
    create_task.assert_called_once()
    created_coro = create_task.call_args[0][0]
    created_coro.close()
    expected_message = get_localizer(app.state.settings.locale).get(
        "admin_notifications.approved_priority"
    )
    assert bot.send_message.call_args.kwargs["text"] == expected_message
