import asyncio
import secrets
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_session
from app.localization import get_localizer
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
from app.services.registrations import (
    CapacityError,
    get_approved_attendee_count,
    update_registration_status,
)

templates = Jinja2Templates(directory="templates")
security = HTTPBasic()


def create_app(settings: Settings, *, bot) -> FastAPI:
    app = FastAPI(title="Event Admin")
    app.state.settings = settings
    app.state.bot = bot

    def current_localizer():
        return get_localizer(app.state.settings.locale)

    def template_context(request: Request, **kwargs: object) -> dict[str, object]:
        return {"request": request, "localizer": current_localizer(), **kwargs}

    def admin_auth(
        request: Request,
        credentials: Annotated[HTTPBasicCredentials, Depends(security)],
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

    def redirect_with_message(path: str, message: str) -> RedirectResponse:
        return RedirectResponse(
            f"{path}?msg={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
        )

    def safe_redirect_path(path: str | None, fallback: str) -> str:
        if not path:
            return fallback
        if not path.startswith("/"):
            return fallback
        if not path.startswith("/admin"):
            return fallback
        return path

    def format_local(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.astimezone(settings.tzinfo).strftime("%Y-%m-%d %H:%M")

    @app.get("/")
    async def index(_: Annotated[str, Depends(admin_auth)]):
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

    @app.get("/admin")
    async def dashboard(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        event = get_or_create_default_event(session, settings)
        status_rows = session.execute(
            select(Registration.status, func.count(Registration.id))
            .where(Registration.event_id == event.id)
            .group_by(Registration.status)
        ).all()
        status_counts = {status: count for status, count in status_rows}
        priority_count = (
            session.scalar(
                select(func.count(Registration.id))
                .where(Registration.event_id == event.id)
                .where(Registration.status == RegistrationStatus.APPROVED)
                .where(Registration.is_priority.is_(True))
            )
            or 0
        )
        category_rows = session.execute(
            select(Registration.category, func.count(Registration.id))
            .where(Registration.event_id == event.id)
            .group_by(Registration.category)
        ).all()
        category_counts = {category.value: count for category, count in category_rows}
        upcoming = (
            session.execute(
                select(ScheduledPost)
                .where(ScheduledPost.sent_at.is_(None))
                .order_by(ScheduledPost.send_at.asc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        upcoming_view = [
            {
                "id": post.id,
                "title": post.title,
                "content": post.content,
                "send_at_local": format_local(post.send_at),
            }
            for post in upcoming
        ]
        status_summary = {
            "approved": status_counts.get(RegistrationStatus.APPROVED, 0),
            "approved_priority": priority_count,
            "waitlisted": status_counts.get(RegistrationStatus.WAITLISTED, 0),
            "declined": status_counts.get(RegistrationStatus.REJECTED, 0),
            "pending": status_counts.get(RegistrationStatus.PENDING, 0),
        }
        category_summary = {
            category.value: category_counts.get(category.value, 0)
            for category in RegistrationCategory
        }
        return templates.TemplateResponse(
            "dashboard.html",
            template_context(
                request,
                event=event,
                status_summary=status_summary,
                category_summary=category_summary,
                upcoming=upcoming_view,
                timezone=settings.timezone,
                timezone_persisted=settings.can_persist_timezone,
                flash=request.query_params.get("msg"),
            ),
        )

    @app.get("/admin/posts")
    async def posts(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
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
        upcoming_view = [
            {
                "id": post.id,
                "title": post.title,
                "content": post.content,
                "send_at_local": format_local(post.send_at),
            }
            for post in upcoming
        ]
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
        sent_view = [
            {
                "id": post.id,
                "title": post.title,
                "content": post.content,
                "sent_at_local": format_local(post.sent_at),
            }
            for post in sent
        ]
        return templates.TemplateResponse(
            "posts.html",
            template_context(
                request,
                upcoming=upcoming_view,
                sent=sent_view,
                event_name=settings.event_name,
                timezone=settings.timezone,
                timezone_persisted=settings.can_persist_timezone,
                flash=request.query_params.get("msg"),
            ),
        )

    @app.post("/admin/posts")
    async def create_post(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
        title: str = Form(...),
        content: str = Form(...),
        send_at: str = Form(...),
    ):
        localizer = current_localizer()
        try:
            send_at_dt = datetime.fromisoformat(send_at)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=localizer.get("admin.errors.invalid_date_format"),
            ) from exc
        if send_at_dt.tzinfo is None:
            send_at_dt = send_at_dt.replace(tzinfo=settings.tzinfo)

        schedule_post(
            session,
            title=title,
            content=content,
            send_at=send_at_dt.astimezone(timezone.utc),
        )
        return redirect_with_message("/admin/posts", localizer.get("admin.posts.flash.scheduled"))

    @app.post("/admin/settings/timezone")
    async def update_timezone(
        _: Annotated[str, Depends(admin_auth)],
        timezone_value: str = Form(...),
        return_to: str | None = Form(None),
    ):
        localizer = current_localizer()
        normalized = timezone_value.strip()
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=localizer.get("admin.errors.timezone_required"),
            )

        try:
            persisted = settings.set_timezone(normalized)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=localizer.get("admin.errors.invalid_timezone"),
            ) from exc

        suffix = ""
        if not persisted:
            suffix = localizer.get("admin.timezone.not_saved_suffix")

        redirect_path = safe_redirect_path(return_to, "/admin")
        return redirect_with_message(
            redirect_path,
            localizer.format("admin.timezone.updated", timezone=normalized, suffix=suffix),
        )

    @app.post("/admin/posts/{post_id}/cancel")
    async def cancel_post(
        request: Request,
        post_id: int,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        localizer = current_localizer()
        post = session.get(ScheduledPost, post_id)
        if not post:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=localizer.get("admin.errors.post_not_found"),
            )
        if post.sent_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=localizer.get("admin.errors.post_already_sent"),
            )
        session.delete(post)
        session.flush()
        return redirect_with_message("/admin/posts", localizer.get("admin.posts.flash.cancelled"))

    def make_action(
        label_key: str,
        status_value: str,
        *,
        priority: bool | None = None,
        css_class: str | None = None,
    ) -> dict[str, str | bool | None]:
        localizer = current_localizer()
        return {
            "label": localizer.get(label_key),
            "status": status_value,
            "priority": priority,
            "class": css_class or "",
        }

    def serialize_registration(
        registration: Registration, actions: list[dict[str, str | bool | None]]
    ) -> dict[str, object]:
        return {
            "id": registration.id,
            "name": registration.user.display_name,
            "contact": registration.user.contact,
            "category": registration.category.value,
            "status": registration.status.value,
            "priority": registration.is_priority,
            "notes": registration.notes or "",
            "actions": actions,
        }

    def render_registration_page(
        request: Request,
        session: Session,
        *,
        event,
        registrations: list[Registration],
        title: str,
        table_title: str,
        empty_message: str,
        actions_builder,
        show_manual_form: bool = False,
        allow_delete: bool = False,
    ):
        registration_views = [
            serialize_registration(registration, actions_builder(registration))
            for registration in registrations
        ]
        pending_count = (
            session.scalar(
                select(func.count(Registration.id))
                .where(Registration.event_id == event.id)
                .where(Registration.status == RegistrationStatus.PENDING)
            )
            or 0
        )
        return templates.TemplateResponse(
            "registrations_list.html",
            template_context(
                request,
                title=title,
                table_title=table_title,
                registrations=registration_views,
                empty_message=empty_message,
                show_manual_form=show_manual_form,
                allow_delete=allow_delete,
                return_to=request.url.path,
                event=event,
                approved_attendee_count=get_approved_attendee_count(session, event),
                pending_count=pending_count,
                flash=request.query_params.get("msg"),
            ),
        )

    def pending_actions(_: Registration) -> list[dict[str, str | bool | None]]:
        return [
            make_action("admin.actions.approve", "approved", priority=False),
            make_action(
                "admin.actions.approve_priority", "approved", priority=True, css_class="secondary"
            ),
            make_action("admin.actions.waitlist", "waitlisted", css_class="secondary"),
            make_action("admin.actions.reject", "rejected", css_class="danger"),
        ]

    def approved_actions(_: Registration) -> list[dict[str, str | bool | None]]:
        return [
            make_action(
                "admin.actions.mark_priority", "approved", priority=True, css_class="secondary"
            ),
            make_action("admin.actions.waitlist", "waitlisted", css_class="secondary"),
            make_action("admin.actions.reject", "rejected", css_class="danger"),
            make_action("admin.actions.reset_pending", "pending", css_class="secondary"),
        ]

    def approved_priority_actions(_: Registration) -> list[dict[str, str | bool | None]]:
        return [
            make_action("admin.actions.mark_regular", "approved", priority=False),
            make_action("admin.actions.waitlist", "waitlisted", css_class="secondary"),
            make_action("admin.actions.reject", "rejected", css_class="danger"),
            make_action("admin.actions.reset_pending", "pending", css_class="secondary"),
        ]

    def waitlisted_actions(_: Registration) -> list[dict[str, str | bool | None]]:
        return [
            make_action("admin.actions.approve", "approved", priority=False),
            make_action(
                "admin.actions.approve_priority", "approved", priority=True, css_class="secondary"
            ),
            make_action("admin.actions.reject", "rejected", css_class="danger"),
            make_action("admin.actions.reset_pending", "pending", css_class="secondary"),
        ]

    def declined_actions(_: Registration) -> list[dict[str, str | bool | None]]:
        return [
            make_action("admin.actions.approve", "approved", priority=False),
            make_action(
                "admin.actions.approve_priority", "approved", priority=True, css_class="secondary"
            ),
            make_action("admin.actions.waitlist", "waitlisted", css_class="secondary"),
            make_action("admin.actions.reset_pending", "pending", css_class="secondary"),
        ]

    def base_registration_query(event, status_filter):
        query = (
            select(Registration)
            .where(Registration.event_id == event.id)
            .order_by(Registration.created_at.asc())
        )
        if status_filter is not None:
            query = query.where(status_filter)
        return query

    @app.get("/admin/registrations")
    async def registrations_pending(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        localizer = current_localizer()
        event = get_or_create_default_event(session, settings)
        registrations = (
            session.execute(
                base_registration_query(event, Registration.status == RegistrationStatus.PENDING)
            )
            .scalars()
            .all()
        )
        return render_registration_page(
            request,
            session,
            event=event,
            registrations=registrations,
            title=localizer.get("admin.registrations.pending.title"),
            table_title=localizer.get("admin.registrations.pending.table_title"),
            empty_message=localizer.get("admin.registrations.pending.empty"),
            actions_builder=pending_actions,
            show_manual_form=True,
            allow_delete=True,
        )

    @app.get("/admin/registrations/approved")
    async def registrations_approved(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        localizer = current_localizer()
        event = get_or_create_default_event(session, settings)
        base_query = base_registration_query(
            event, Registration.status == RegistrationStatus.APPROVED
        ).where(Registration.is_priority.is_(False))
        registrations = session.execute(base_query).scalars().all()
        return render_registration_page(
            request,
            session,
            event=event,
            registrations=registrations,
            title=localizer.get("admin.registrations.approved.title"),
            table_title=localizer.get("admin.registrations.approved.table_title"),
            empty_message=localizer.get("admin.registrations.approved.empty"),
            actions_builder=approved_actions,
        )

    @app.get("/admin/registrations/approved-priority")
    async def registrations_approved_priority(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        localizer = current_localizer()
        event = get_or_create_default_event(session, settings)
        base_query = base_registration_query(
            event, Registration.status == RegistrationStatus.APPROVED
        ).where(Registration.is_priority.is_(True))
        registrations = session.execute(base_query).scalars().all()
        return render_registration_page(
            request,
            session,
            event=event,
            registrations=registrations,
            title=localizer.get("admin.registrations.priority.title"),
            table_title=localizer.get("admin.registrations.priority.table_title"),
            empty_message=localizer.get("admin.registrations.priority.empty"),
            actions_builder=approved_priority_actions,
        )

    @app.get("/admin/registrations/waitlisted")
    async def registrations_waitlisted(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        localizer = current_localizer()
        event = get_or_create_default_event(session, settings)
        registrations = (
            session.execute(
                base_registration_query(event, Registration.status == RegistrationStatus.WAITLISTED)
            )
            .scalars()
            .all()
        )
        return render_registration_page(
            request,
            session,
            event=event,
            registrations=registrations,
            title=localizer.get("admin.registrations.waitlisted.title"),
            table_title=localizer.get("admin.registrations.waitlisted.table_title"),
            empty_message=localizer.get("admin.registrations.waitlisted.empty"),
            actions_builder=waitlisted_actions,
        )

    @app.get("/admin/registrations/declined")
    async def registrations_declined(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
    ):
        localizer = current_localizer()
        event = get_or_create_default_event(session, settings)
        registrations = (
            session.execute(
                base_registration_query(event, Registration.status == RegistrationStatus.REJECTED)
            )
            .scalars()
            .all()
        )
        return render_registration_page(
            request,
            session,
            event=event,
            registrations=registrations,
            title=localizer.get("admin.registrations.declined.title"),
            table_title=localizer.get("admin.registrations.declined.table_title"),
            empty_message=localizer.get("admin.registrations.declined.empty"),
            actions_builder=declined_actions,
        )

    @app.post("/admin/registrations/{registration_id}/status")
    async def update_status(
        request: Request,
        registration_id: int,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
        status_value: str = Form(...),
        priority_value: str | None = Form(None),
        return_to: str | None = Form(None),
    ):
        localizer = current_localizer()
        registration = session.get(Registration, registration_id)
        if not registration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=localizer.get("admin.errors.registration_not_found"),
            )

        try:
            status_enum = RegistrationStatus(status_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=localizer.get("admin.errors.unknown_status"),
            ) from exc

        if priority_value is None:
            priority_flag: bool | None = None
        else:
            priority_flag = priority_value.lower() in {"true", "1", "on"}

        try:
            update_registration_status(
                session,
                registration,
                status=status_enum,
                is_priority=priority_flag,
            )
        except CapacityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=localizer.get("admin.errors.attendee_limit_reached"),
            ) from exc

        session.flush()

        if registration.user.telegram_id and registration.user.notifications_enabled:
            message = None
            localizer = current_localizer()
            if status_enum == RegistrationStatus.APPROVED:
                if getattr(registration, "is_priority", False):
                    message = localizer.get("admin_notifications.approved_priority")
                else:
                    message = localizer.get("admin_notifications.approved")
            elif status_enum == RegistrationStatus.REJECTED:
                message = localizer.get("admin_notifications.rejected")
            elif status_enum == RegistrationStatus.WAITLISTED:
                message = localizer.get("admin_notifications.waitlisted")
            if message:
                asyncio.create_task(
                    request.app.state.bot.send_message(
                        chat_id=registration.user.telegram_id, text=message
                    )
                )

        redirect_path = safe_redirect_path(return_to, "/admin/registrations")
        return RedirectResponse(redirect_path, status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/registrations/{registration_id}/delete")
    async def delete_registration(
        request: Request,
        registration_id: int,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
        return_to: str | None = Form(None),
    ):
        localizer = current_localizer()
        registration = session.get(Registration, registration_id)
        if not registration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=localizer.get("admin.errors.registration_not_found"),
            )
        session.delete(registration)
        session.flush()
        redirect_path = safe_redirect_path(return_to, "/admin/registrations")
        return redirect_with_message(
            redirect_path, localizer.get("admin.registrations.flash.deleted")
        )

    @app.post("/admin/registrations/manual")
    async def add_manual_registration(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
        display_name: str = Form(...),
        contact: str | None = Form(None),
        category_value: str = Form(...),
        notes: str | None = Form(None),
        priority: bool = Form(False),
        return_to: str | None = Form(None),
    ):
        localizer = current_localizer()
        try:
            category = RegistrationCategory(category_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=localizer.get("admin.errors.unknown_category"),
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
        redirect_path = safe_redirect_path(return_to, "/admin/registrations")
        return RedirectResponse(redirect_path, status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/admin/urgent")
    async def urgent(request: Request, _: Annotated[str, Depends(admin_auth)]):
        return templates.TemplateResponse(
            "urgent.html",
            template_context(request),
        )

    @app.post("/admin/urgent")
    async def send_urgent(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
        message: str = Form(...),
    ):
        delivered = await broadcast_message(session, request.app.state.bot, message)
        return templates.TemplateResponse(
            "urgent.html",
            template_context(request, delivered=delivered, message=message),
        )

    @app.post("/admin/event/limit")
    async def update_limit(
        request: Request,
        _: Annotated[str, Depends(admin_auth)],
        session: Annotated[Session, Depends(get_session)],
        limit: str = Form(...),
    ):
        event = get_or_create_default_event(session, settings)
        localizer = current_localizer()

        normalized = limit.strip()
        if not normalized:
            event.capacity = None
            object.__setattr__(settings, "attendee_limit", None)
            message = localizer.get("admin.registrations.flash.limit_removed")
        else:
            try:
                new_limit = int(normalized)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=localizer.get("admin.errors.limit_integer"),
                ) from exc
            if new_limit < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=localizer.get("admin.errors.limit_non_negative"),
                )
            event.capacity = new_limit
            object.__setattr__(settings, "attendee_limit", new_limit)
            message = localizer.get("admin.registrations.flash.limit_updated")

        session.flush()
        return redirect_with_message("/admin/registrations", message)

    return app
