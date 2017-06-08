import asyncio
import secrets
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_session
from app.models import (
    Registration,
    RegistrationCategory,
    RegistrationStatus,
    ScheduledPost,
)
from app.services import admin as admin_service
from app.services.events import get_or_create_default_event
from app.services.messaging import broadcast_message
from app.services.posts import schedule_post
from app.services.registrations import CapacityError, update_registration_status


templates = Jinja2Templates(directory="templates")
security = HTTPBasic()


def create_app(settings: Settings, *, bot) -> FastAPI:
    app = FastAPI(title="Event Admin")
    app.state.settings = settings
    app.state.bot = bot

    def admin_auth(
        request: Request,
        credentials: HTTPBasicCredentials = Depends(security),
    ) -> str:
        correct_username = secrets.compare_digest(
            credentials.username, settings.basic_auth_username
        )
        correct_password = secrets.compare_digest(
            credentials.password, settings.basic_auth_password
        )
        if not (correct_username and correct_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"}
            )
        return credentials.username

    @app.get("/")
    async def index(_: str = Depends(admin_auth)):
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/posts")
    async def posts(
        request: Request, _: str = Depends(admin_auth), session: Session = Depends(get_session)
    ):
        upcoming = (
            session.execute(
                select(ScheduledPost)
                .where(ScheduledPost.sent_at.is_(None))
                .order_by(ScheduledPost.send_at.asc())
            )
            .scalars()
            .all()
        )
        sent = (
            session.execute(
                select(ScheduledPost)
                .where(ScheduledPost.sent_at.is_not(None))
                .order_by(ScheduledPost.sent_at.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        return templates.TemplateResponse(
            "posts.html",
            {
                "request": request,
                "upcoming": upcoming,
                "sent": sent,
                "event_name": settings.event_name,
            },
        )

    @app.post("/admin/posts")
    async def create_post(
        request: Request,
        title: str = Form(...),
        content: str = Form(...),
        send_at: str = Form(...),
        _: str = Depends(admin_auth),
        session: Session = Depends(get_session),
    ):
        try:
            send_at_dt = datetime.fromisoformat(send_at)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date format"
            ) from exc
        if send_at_dt.tzinfo is None:
            send_at_dt = send_at_dt.replace(tzinfo=timezone.utc)

        schedule_post(session, title=title, content=content, send_at=send_at_dt)
        return RedirectResponse("/admin/posts", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/admin/registrations")
    async def registrations(
        request: Request,
        _: str = Depends(admin_auth),
        session: Session = Depends(get_session),
    ):
        attendees = (
            session.execute(
                select(Registration)
                .where(Registration.category == RegistrationCategory.ATTENDEE)
                .order_by(Registration.created_at.asc())
            )
            .scalars()
            .all()
        )
        lecturers = (
            session.execute(
                select(Registration)
                .where(
                    Registration.category.in_(
                        [RegistrationCategory.LECTURER, RegistrationCategory.SHOWCASE]
                    )
                )
                .order_by(Registration.created_at.asc())
            )
            .scalars()
            .all()
        )
        return templates.TemplateResponse(
            "registrations.html",
            {
                "request": request,
                "attendees": attendees,
                "lecturers": lecturers,
                "RegistrationStatus": RegistrationStatus,
            },
        )

    @app.post("/admin/registrations/{registration_id}/status")
    async def update_status(
        request: Request,
        registration_id: int,
        status_value: str = Form(...),
        priority: bool = Form(False),
        _: str = Depends(admin_auth),
        session: Session = Depends(get_session),
    ):
        registration = session.get(Registration, registration_id)
        if not registration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found"
            )

        try:
            status_enum = RegistrationStatus(status_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown status"
            ) from exc

        try:
            update_registration_status(
                session, registration, status=status_enum, is_priority=priority
            )
        except CapacityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Attendee limit reached"
            )

        session.flush()

        if registration.user.telegram_id:
            message = None
            if status_enum == RegistrationStatus.APPROVED:
                message = "You're approved for the event! See you soon."
            elif status_enum == RegistrationStatus.REJECTED:
                message = "Unfortunately we can't confirm your spot this time."
            elif status_enum == RegistrationStatus.WAITLISTED:
                message = "You're still on the waiting list. We'll keep you updated."
            if message:
                asyncio.create_task(
                    request.app.state.bot.send_message(
                        chat_id=registration.user.telegram_id, text=message
                    )
                )

        return RedirectResponse("/admin/registrations", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/registrations/manual")
    async def add_manual_registration(
        request: Request,
        display_name: str = Form(...),
        contact: str | None = Form(None),
        category_value: str = Form(...),
        notes: str | None = Form(None),
        priority: bool = Form(False),
        _: str = Depends(admin_auth),
        session: Session = Depends(get_session),
    ):
        try:
            category = RegistrationCategory(category_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown category"
            ) from exc

        event = get_or_create_default_event(session, settings)
        admin_service.create_manual_attendee(
            session,
            event=event,
            display_name=display_name,
            contact=contact,
            category=category,
            notes=notes,
            is_priority=priority,
        )
        return RedirectResponse("/admin/registrations", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/admin/urgent")
    async def urgent(request: Request, _: str = Depends(admin_auth)):
        return templates.TemplateResponse(
            "urgent.html",
            {
                "request": request,
            },
        )

    @app.post("/admin/urgent")
    async def send_urgent(
        request: Request,
        message: str = Form(...),
        _: str = Depends(admin_auth),
        session: Session = Depends(get_session),
    ):
        delivered = await broadcast_message(session, request.app.state.bot, message)
        return templates.TemplateResponse(
            "urgent.html",
            {
                "request": request,
                "delivered": delivered,
                "message": message,
            },
        )

    return app
