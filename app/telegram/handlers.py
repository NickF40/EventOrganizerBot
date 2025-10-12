from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import session_scope
from app.models import Registration, RegistrationCategory, RegistrationStatus
from app.services.events import get_or_create_default_event
from app.services.registrations import register_user
from app.services.users import get_or_create_user


REGISTRATION_CALLBACK_PREFIX = "register:"


def build_registration_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="Attend the event",
                callback_data=f"{REGISTRATION_CALLBACK_PREFIX}{RegistrationCategory.ATTENDEE.value}",
            )
        ],
        [
            InlineKeyboardButton(
                text="Register as lecturer",
                callback_data=f"{REGISTRATION_CALLBACK_PREFIX}{RegistrationCategory.LECTURER.value}",
            )
        ],
        [
            InlineKeyboardButton(
                text="Project showcase",
                callback_data=f"{REGISTRATION_CALLBACK_PREFIX}{RegistrationCategory.SHOWCASE.value}",
            )
        ],
    ]
    return InlineKeyboardMarkup(buttons)


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

    if registrations:
        lines: list[str] = []
        for registration in registrations:
            status_text = registration.status.value.replace("_", " ").title()
            if registration.status == RegistrationStatus.WAITLISTED:
                status_text += " (waiting list)"
            elif registration.status == RegistrationStatus.APPROVED:
                status_text += " (approved)"
            lines.append(f"• {registration.category.value.title()}: {status_text}")
        summary = "\n".join(lines)
        welcome_text = (
            f"Welcome back, {user.first_name or user.full_name or user.username}!\n\n"
            "We already have you registered:\n"
            f"{summary}\n\n"
            "Use /status any time for the latest updates or pick another option below."
        )
    else:
        welcome_text = (
            f"Hi {user.first_name or user.full_name or user.username}!\n\n"
            f"Welcome to the {settings.event_name} bot. Choose how you'd like to participate."
        )
    await context.bot.send_message(
        chat_id=chat.id, text=welcome_text, reply_markup=build_registration_keyboard()
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
    try:
        category = RegistrationCategory(category_value)
    except ValueError:
        await query.edit_message_text("Unknown registration option. Please try again.")
        return

    settings: Settings = get_settings()
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
                message = "You're on the waiting list. We'll let you know if a spot opens up!"
            else:
                message = "Thanks! Your attendance request has been received. An organiser will confirm soon."
        elif category == RegistrationCategory.LECTURER:
            message = "Thanks! We've received your lecturer application. We'll follow up shortly."
        else:
            message = "Great! Your project showcase registration is in our system."
    else:
        message = "We already have your registration on file. We'll keep you posted!"

    await query.edit_message_text(message)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if not registrations:
        await context.bot.send_message(
            chat_id=chat.id,
            text="We couldn't find a registration for you yet. Use /start to sign up!",
        )
        return

    parts = []
    for registration in registrations:
        status_text = registration.status.value.replace("_", " ").title()
        if registration.status == RegistrationStatus.WAITLISTED:
            status_text += " (waiting list)"
        elif registration.status == RegistrationStatus.APPROVED:
            status_text += " (approved)"
        parts.append(f"{registration.category.value.title()}: {status_text}")

    await context.bot.send_message(chat_id=chat.id, text="\n".join(parts))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text="Use /start to begin registration or /status to check your current status.",
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(handle_registration_callback))
