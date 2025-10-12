from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import sys
import types

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_TOKEN", "dummy-token")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "secret")

sys.modules.setdefault("telegram", types.SimpleNamespace(Bot=object))
sys.modules.setdefault("telegram.error", types.SimpleNamespace(TelegramError=Exception))

from app.config import Settings
from app.web import admin as admin_module
from app.database import get_session


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


def test_index_redirects_to_posts(admin_client):
    client, app, _ = admin_client
    session = MagicMock()
    app.dependency_overrides[get_session] = _override_session(session)

    response = client.get("/", auth=("admin", "secret"), follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/posts"


def test_posts_page_renders_lists(admin_client):
    client, app, _ = admin_client
    upcoming_post = SimpleNamespace(title="Future", send_at=datetime.now(timezone.utc))
    sent_post = SimpleNamespace(title="Past", sent_at=datetime.now(timezone.utc))

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
            "send_at": "2024-01-01T12:00:00+00:00",
        },
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/posts"
    schedule_post.assert_called_once()
    _, kwargs = schedule_post.call_args
    assert kwargs["title"] == "Hello"
    assert kwargs["content"] == "World"
    assert kwargs["send_at"].tzinfo is not None


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

    registration = SimpleNamespace(user=SimpleNamespace(telegram_id=123))
    session.get.return_value = registration

    update_status = MagicMock()
    monkeypatch.setattr(admin_module, "update_registration_status", update_status)

    create_task = MagicMock()
    monkeypatch.setattr(admin_module.asyncio, "create_task", create_task)

    response = client.post(
        "/admin/registrations/1/status",
        data={"status_value": "approved", "priority": "on"},
        auth=("admin", "secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    update_status.assert_called_once()
    create_task.assert_called_once()
    created_coro = create_task.call_args[0][0]
    created_coro.close()

