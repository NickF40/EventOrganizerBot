from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Event


def get_or_create_default_event(session: Session, settings: Settings) -> Event:
    event = session.scalar(select(Event).where(Event.name == settings.event_name))
    if event:
        if event.capacity is None and settings.attendee_limit is not None:
            event.capacity = settings.attendee_limit
        return event

    event = Event(name=settings.event_name, capacity=settings.attendee_limit)
    session.add(event)
    session.flush()
    return event
