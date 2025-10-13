import pytest

from app.models import Event, Registration, RegistrationCategory, RegistrationStatus, User
from app.services import registrations
from tests.test_events import make_session


def create_user(session, *, telegram_id: int, name: str) -> User:
    user = User(
        telegram_id=telegram_id,
        username=name,
        first_name=name,
        last_name=None,
        display_name=name,
        contact=name,
        is_manual=False,
    )
    session.add(user)
    session.flush()
    return user


def create_event(session, *, capacity: int | None) -> Event:
    event = Event(name="Test", capacity=capacity)
    session.add(event)
    session.flush()
    return event


def approve_registration(session, registration: Registration) -> None:
    registration.status = RegistrationStatus.APPROVED
    session.add(registration)
    session.flush()


def test_register_user_waitlists_when_full():
    with make_session() as session:
        event = create_event(session, capacity=1)
        existing_user = create_user(session, telegram_id=1, name="existing")
        new_user = create_user(session, telegram_id=2, name="new")

        existing = registrations.register_user(
            session,
            event=event,
            user=existing_user,
            category=RegistrationCategory.ATTENDEE,
        )
        approve_registration(session, existing.registration)

        result = registrations.register_user(
            session,
            event=event,
            user=new_user,
            category=RegistrationCategory.ATTENDEE,
        )

        assert result.created is True
        assert result.waitlisted is True
        assert result.registration.status == RegistrationStatus.WAITLISTED


def test_update_registration_status_enforces_capacity():
    with make_session() as session:
        event = create_event(session, capacity=1)
        user_one = create_user(session, telegram_id=1, name="one")
        user_two = create_user(session, telegram_id=2, name="two")

        reg_one = registrations.register_user(
            session,
            event=event,
            user=user_one,
            category=RegistrationCategory.ATTENDEE,
        ).registration
        reg_two = registrations.register_user(
            session,
            event=event,
            user=user_two,
            category=RegistrationCategory.ATTENDEE,
        ).registration

        registrations.update_registration_status(
            session,
            reg_one,
            status=RegistrationStatus.APPROVED,
        )
        with pytest.raises(registrations.CapacityError):
            registrations.update_registration_status(
                session,
                reg_two,
                status=RegistrationStatus.APPROVED,
            )


def test_update_registration_status_allows_priority_override():
    with make_session() as session:
        event = create_event(session, capacity=1)
        primary = create_user(session, telegram_id=1, name="primary")
        priority = create_user(session, telegram_id=2, name="priority")

        reg_primary = registrations.register_user(
            session,
            event=event,
            user=primary,
            category=RegistrationCategory.ATTENDEE,
        ).registration
        reg_priority = registrations.register_user(
            session,
            event=event,
            user=priority,
            category=RegistrationCategory.ATTENDEE,
        ).registration

        registrations.update_registration_status(
            session,
            reg_primary,
            status=RegistrationStatus.APPROVED,
        )

        registrations.update_registration_status(
            session,
            reg_priority,
            status=RegistrationStatus.APPROVED,
            is_priority=True,
        )

        assert reg_priority.is_priority is True
        assert reg_priority.status == RegistrationStatus.APPROVED
