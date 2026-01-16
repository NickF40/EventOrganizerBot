import importlib
from datetime import datetime, timedelta

import app.config as config
import app.telebot.db as telebot_db
from app.models import UserStatus


def _configure_settings(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
    monkeypatch.setenv("ADMIN_USERNAMES", "admin")
    config.get_settings.cache_clear()


def test_parse_helpers(monkeypatch):
    _configure_settings(monkeypatch)

    assert telebot_db._parse_user_id("42") == 42
    assert telebot_db._parse_user_id("42.0") == 42
    assert telebot_db._parse_user_id("42.5") is None
    assert telebot_db._parse_user_id("nope") is None
    assert telebot_db._parse_user_id(" ") is None
    assert telebot_db.is_admin(None) is False

    assert telebot_db._parse_status("attendee") == UserStatus.ATTENDEE
    assert telebot_db._parse_status("WAITLIST") == UserStatus.WAITLIST
    assert telebot_db._parse_status("unknown") is None
    assert telebot_db._parse_status(None) is None

    assert telebot_db._parse_bool("yes") is True
    assert telebot_db._parse_bool("0") is False
    assert telebot_db._parse_bool("maybe") is None
    assert telebot_db._parse_bool(None) is None

    assert telebot_db._normalize_friend_usernames("@Alice, bob  ,bob") == "alice,bob"


def test_build_user_from_row(monkeypatch):
    _configure_settings(monkeypatch)

    assert telebot_db._build_user_from_row({"username": "missing"}) is None
    assert telebot_db._build_user_from_row({"user_id": "oops"}) is None

    row = {
        "user_id": "100",
        "username": "member",
        "full_name": "Ada Lovelace",
        "job": "Engineer",
        "career_path": "Backend",
        "status": "processing",
        "notifications_enabled": "true",
        "friend_usernames": "@buddy, pal",
    }
    user = telebot_db._build_user_from_row(row)
    assert user is not None
    assert user.telegram_id == 100
    assert user.status == UserStatus.PROCESSING
    assert user.friend_usernames == "buddy,pal"

    invalid_status = {
        "user_id": "102",
        "username": "member",
        "status": "not-a-status",
        "notifications_enabled": "true",
    }
    assert telebot_db._build_user_from_row(invalid_status) is None

    admin_row = {
        "user_id": "101",
        "username": "admin",
        "full_name": "Admin",
        "job": "",
        "career_path": "",
        "status": "attendee",
        "notifications_enabled": "true",
    }
    assert telebot_db._build_user_from_row(admin_row) is None


def test_normalize_row_and_schema_validation(monkeypatch):
    _configure_settings(monkeypatch)

    normalized = telebot_db._normalize_row({" user_id ": " 42 ", None: "skip"})
    assert normalized == {"user_id": "42"}
    normalized = telebot_db._normalize_row({"user_id": None})
    assert normalized == {"user_id": None}

    assert telebot_db._validate_schema(
        [
            "user_id",
            "username",
            "full_name",
            "job",
            "career_path",
            "status",
            "notifications_enabled",
        ]
    )
    assert telebot_db._validate_schema(["user_id", "username"]) is False
    assert telebot_db._validate_schema(None) is False


def test_admin_state_and_template_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    config.get_settings.cache_clear()

    import app.database as database
    import app.models as models
    import app.telebot.db as db_module

    database = importlib.reload(database)
    models = importlib.reload(models)
    db_module = importlib.reload(db_module)
    database.ensure_schema()

    with database.session_scope() as session:
        db_module.set_template(session, "welcome_message", 1, 10)
        session.flush()
        db_module.set_template(session, "welcome_message", 2, 20)
        template = session.get(models.MessageTemplate, "welcome_message")

    assert template.admin_chat_id == 2
    assert template.message_id == 20

    with database.session_scope() as session:
        session.add(
            models.AdminState(
                admin_id=1,
                waiting_for=models.AdminStateType.WELCOME,
                ttl_seconds=300,
                created_at=datetime.utcnow() - timedelta(seconds=400),
            )
        )

    with database.session_scope() as session:
        assert db_module.get_admin_state(session, 1) is None
