from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Event, Registration, RegistrationCategory, RegistrationStatus, User


class CapacityError(RuntimeError):
    """Raised when the attendee limit has been reached."""


@dataclass
class RegistrationResult:
    registration: Registration
    created: bool
    waitlisted: bool


def approved_attendee_count_query(event: Event) -> Select[tuple[int]]:
    return (
        select(func.count(Registration.id))
        .where(Registration.event_id == event.id)
        .where(Registration.category == RegistrationCategory.ATTENDEE)
        .where(Registration.status == RegistrationStatus.APPROVED)
    )


def get_approved_attendee_count(session: Session, event: Event) -> int:
    return session.scalar(approved_attendee_count_query(event)) or 0


def find_registration(
    session: Session, event: Event, user: User, category: RegistrationCategory
) -> Registration | None:
    return session.scalar(
        select(Registration)
        .where(Registration.event_id == event.id)
        .where(Registration.user_id == user.id)
        .where(Registration.category == category)
    )


def register_user(
    session: Session,
    *,
    event: Event,
    user: User,
    category: RegistrationCategory,
    notes: str | None = None,
) -> RegistrationResult:
    registration = find_registration(session, event, user, category)
    created = False
    waitlisted = False

    if registration:
        registration.notes = notes
        return RegistrationResult(registration=registration, created=created, waitlisted=waitlisted)

    status = RegistrationStatus.PENDING
    if category == RegistrationCategory.ATTENDEE:
        if (
            event.capacity is not None
            and get_approved_attendee_count(session, event) >= event.capacity
        ):
            status = RegistrationStatus.WAITLISTED
            waitlisted = True
    else:
        status = RegistrationStatus.PENDING

    registration = Registration(
        event_id=event.id,
        user_id=user.id,
        category=category,
        status=status,
        notes=notes,
    )
    session.add(registration)
    session.flush()
    created = True
    return RegistrationResult(registration=registration, created=created, waitlisted=waitlisted)


def update_registration_status(
    session: Session,
    registration: Registration,
    *,
    status: RegistrationStatus,
    is_priority: bool | None = None,
    enforce_capacity: bool = True,
) -> None:
    if status == RegistrationStatus.APPROVED and enforce_capacity:
        event = registration.event
        if (
            registration.category == RegistrationCategory.ATTENDEE
            and not (is_priority or registration.is_priority)
            and event.capacity is not None
        ):
            approved_count = get_approved_attendee_count(session, event)
            currently_approved = 1 if registration.status == RegistrationStatus.APPROVED else 0
            if approved_count - currently_approved >= event.capacity:
                raise CapacityError("Attendee limit has been reached.")

    registration.status = status
    if is_priority is not None:
        registration.is_priority = is_priority
    session.flush()
