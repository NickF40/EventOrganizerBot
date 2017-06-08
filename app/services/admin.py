from sqlalchemy.orm import Session

from app.models import Event, Registration, RegistrationCategory, RegistrationStatus, User


def create_manual_attendee(
    session: Session,
    *,
    event: Event,
    display_name: str,
    contact: str | None,
    category: RegistrationCategory,
    notes: str | None = None,
    is_priority: bool = True,
    status: RegistrationStatus = RegistrationStatus.APPROVED,
) -> Registration:
    user = User(
        telegram_id=None,
        username=None,
        first_name=None,
        last_name=None,
        display_name=display_name,
        contact=contact,
        is_manual=True,
    )
    session.add(user)
    session.flush()

    registration = Registration(
        user_id=user.id,
        event_id=event.id,
        category=category,
        status=status,
        is_priority=is_priority,
        notes=notes,
    )
    session.add(registration)
    session.flush()
    return registration
