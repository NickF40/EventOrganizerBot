from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base
from app.models import Event
from app.services.events import get_or_create_default_event


def make_settings(**overrides) -> Settings:
    defaults = dict(
        telegram_token="token",
        basic_auth_username="admin",
        basic_auth_password="secret",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@contextmanager
def make_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    TestingSession = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_event_created_with_attendee_limit():
    settings = make_settings(event_name="Test Event", attendee_limit=99)
    with make_session() as session:
        event = get_or_create_default_event(session, settings)

        assert event.id is not None
        assert event.name == "Test Event"
        assert event.capacity == 99

        stored = session.get(Event, event.id)
        assert stored is not None
        assert stored.capacity == 99


def test_existing_event_capacity_backfilled():
    settings = make_settings(event_name="Existing Event", attendee_limit=25)
    with make_session() as session:
        event = Event(name="Existing Event", capacity=None)
        session.add(event)
        session.flush()

        fetched = get_or_create_default_event(session, settings)

        assert fetched.id == event.id
        assert fetched.capacity == 25


def test_existing_event_capacity_preserved():
    settings = make_settings(event_name="Existing Event", attendee_limit=50)
    with make_session() as session:
        event = Event(name="Existing Event", capacity=75)
        session.add(event)
        session.flush()

        fetched = get_or_create_default_event(session, settings)

        assert fetched.id == event.id
        assert fetched.capacity == 75
