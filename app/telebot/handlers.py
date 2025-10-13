from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import session_scope
from app.localization import get_localizer
from app.models import Registration, RegistrationCategory, RegistrationStatus
from app.services.events import get_or_create_default_event
from app.services.registrations import register_user
from app.services.users import get_or_create_user


REGISTRATION_CALLBACK_PREFIX = "register:"


def build_registration_keyboard(localizer) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=localizer.get("buttons.attend"),
                callback_data=(
                    f"{REGISTRATION_CALLBACK_PREFIX}{RegistrationCategory.ATTENDEE.value}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                text=localizer.get("buttons.lecturer"),
                callback_data=(
                    f"{REGISTRATION_CALLBACK_PREFIX}{RegistrationCategory.LECTURER.value}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                text=localizer.get("buttons.showcase"),
                callback_data=(
                    f"{REGISTRATION_CALLBACK_PREFIX}{RegistrationCategory.SHOWCASE.value}"
                ),
            )
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def _localized_status(localizer, registration: Registration) -> str:
    base = localizer.get(f"registration.status.{registration.status.value}")
    if registration.status == RegistrationStatus.WAITLISTED:
        suffix = localizer.get("registration.status.waitlisted_suffix")
        return f"{base}{suffix}"
    if registration.status == RegistrationStatus.APPROVED:
        suffix = localizer.get("registration.status.approved_suffix")
        return f"{base}{suffix}"
    return base


def _localized_category(localizer, category: RegistrationCategory) -> str:
    return localizer.get(f"registration.category.{category.value}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    settings = get_settings()

    with session_scope() as session:
        db_user = get_or_create_user(session, user)
        event = get_or_create_default_event(session, settings)
        registrations = (
            session.execute(
                select(Registration)
                .where(Registration.user_id == db_user.id)
                .where(Registration.event_id == event.id)
            )
            .scalars()
            .all()
        )

    localizer = get_localizer(settings.locale)

    if registrations:
        lines: list[str] = []
        for registration in registrations:
            status_text = _localized_status(localizer, registration)
            category_text = _localized_category(localizer, registration.category)
            lines.append(
                localizer.format(
                    "start.summary_item", category=category_text, status=status_text
                )
            )
        summary = "\n".join(lines)
        welcome_text = localizer.format(
            "start.returning",
            name=user.first_name or user.full_name or user.username,
            summary=summary,
        )
    else:
        welcome_text = localizer.format(
            "start.new",
            name=user.first_name or user.full_name or user.username,
            event_name=settings.event_name,
        )
    await context.bot.send_message(
        chat_id=chat.id,
        text=welcome_text,
        reply_markup=build_registration_keyboard(localizer),
    )


async def handle_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if not data.startswith(REGISTRATION_CALLBACK_PREFIX):
        return

    category_value = data.split(":", 1)[1]
    settings: Settings = get_settings()
    localizer = get_localizer(settings.locale)

    try:
        category = RegistrationCategory(category_value)
    except ValueError:
        await query.edit_message_text(localizer.get("registration.callback.unknown_option"))
        return

    user = query.from_user
    if not user:
        return

    with session_scope() as session:
        db_user = get_or_create_user(session, user)
        event = get_or_create_default_event(session, settings)
        result = register_user(session, event=event, user=db_user, category=category)

    if result.created:
        if category == RegistrationCategory.ATTENDEE:
            if result.waitlisted:
                message = localizer.get("registration.callback.waitlisted")
            else:
                message = localizer.get("registration.callback.attendee")
        elif category == RegistrationCategory.LECTURER:
            message = localizer.get("registration.callback.lecturer")
        else:
            message = localizer.get("registration.callback.showcase")
    else:
        message = localizer.get("registration.callback.duplicate")

    await query.edit_message_text(message)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    settings = get_settings()
    localizer = get_localizer(settings.locale)

    with session_scope() as session:
        db_user = get_or_create_user(session, user)
        event = get_or_create_default_event(session, settings)
        registrations = (
            session.execute(
                select(Registration)
                .where(Registration.user_id == db_user.id)
                .where(Registration.event_id == event.id)
            )
            .scalars()
            .all()
        )

    if not registrations:
        await context.bot.send_message(
            chat_id=chat.id,
            text=localizer.get("status.none_found"),
        )
        return

    parts = []
    for registration in registrations:
        status_text = _localized_status(localizer, registration)
        category_text = _localized_category(localizer, registration.category)
        parts.append(localizer.format("status.line", category=category_text, status=status_text))

    await context.bot.send_message(chat_id=chat.id, text="\n".join(parts))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    settings = get_settings()
    localizer = get_localizer(settings.locale)
    await context.bot.send_message(
        chat_id=chat.id,
        text=localizer.get("help.text"),
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(handle_registration_callback))
