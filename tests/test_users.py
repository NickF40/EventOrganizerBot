from types import SimpleNamespace

from app.models import User
from app.services import users
from tests.test_events import make_session


def test_resolve_display_name_prefers_full_name():
    tg_user = SimpleNamespace(full_name="Full Name", username="user", id=1)
    assert users.resolve_display_name(tg_user) == "Full Name"


def test_get_or_create_user_updates_existing():
    with make_session() as session:
        existing = User(
            telegram_id=1,
            username="old",
            first_name="Old",
            last_name=None,
            display_name="Old",
            contact="old",
            is_manual=False,
        )
        session.add(existing)
        session.commit()

        tg_user = SimpleNamespace(
            id=1,
            username="new",
            first_name="New",
            last_name="Name",
            full_name="New Name",
        )

        updated = users.get_or_create_user(session, tg_user)

        assert updated.id == existing.id
        assert updated.username == "new"
        assert updated.display_name == "New Name"
        assert updated.contact == "new"
        assert updated.is_subscribed is True


def test_get_or_create_user_creates_new_user():
    with make_session() as session:
        tg_user = SimpleNamespace(
            id=2,
            username="fresh",
            first_name="Fresh",
            last_name=None,
            full_name=None,
        )

        created = users.get_or_create_user(session, tg_user)

        assert created.id is not None
        assert created.display_name == "fresh"
        assert created.is_manual is False
