from app.models import Event, RegistrationCategory
from app.services import admin
from tests.test_events import make_session


def test_create_manual_attendee_creates_user_and_registration():
    with make_session() as session:
        event = Event(name="Test", capacity=10)
        session.add(event)
        session.commit()

        registration = admin.create_manual_attendee(
            session,
            event=event,
            display_name="Guest",
            contact="guest@example.com",
            category=RegistrationCategory.ATTENDEE,
            notes="VIP",
            is_priority=False,
        )

        assert registration.user.display_name == "Guest"
        assert registration.category == RegistrationCategory.ATTENDEE
        assert registration.notes == "VIP"
        assert registration.is_priority is False
