import asyncio
import csv
import io
import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.config import get_settings
from app.database import session_scope
from app.localization import DEFAULT_LOCALE, get_localizer
from app.models import AdminStateType, Feedback, MessageTemplate, User, UserStatus
from app.telebot.db import (
    clear_admin_state,
    get_admin_state,
    get_or_create_event_state,
    get_template,
    is_admin,
    process_upload_database,
    set_admin_state,
    set_template,
    upsert_user,
)

logger = logging.getLogger(__name__)


def get_bot_localizer():
    settings = get_settings()
    locale = getattr(settings, "locale", DEFAULT_LOCALE)
    return get_localizer(locale)


LOCALIZER = get_bot_localizer()

MENU_APPLICATION = LOCALIZER.get("bot.menu.application")
MENU_CANCEL = LOCALIZER.get("bot.menu.cancel")
MENU_FEEDBACK = LOCALIZER.get("bot.menu.feedback")
MENU_SCHEDULE = LOCALIZER.get("bot.menu.schedule")
MENU_STATUS = LOCALIZER.get("bot.menu.status")
MENU_NOTIFICATIONS = LOCALIZER.get("bot.menu.notifications")
MENU_HOME = LOCALIZER.get("bot.menu.home")

APPLICATION_FULL_NAME = 1
APPLICATION_JOB = 2
APPLICATION_CAREER = 3
APPLICATION_FRIENDS = 4

FEEDBACK_TEXT = 10


def build_main_keyboard(status: UserStatus, event_started: bool) -> ReplyKeyboardMarkup:
    if status == UserStatus.NONE:
        first_button = MENU_APPLICATION
    elif event_started:
        first_button = MENU_FEEDBACK
    else:
        first_button = MENU_CANCEL
    keyboard = [
        [first_button, MENU_SCHEDULE],
        [MENU_STATUS, MENU_NOTIFICATIONS],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def home_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[MENU_HOME]], resize_keyboard=True)


def notifications_text(enabled: bool) -> str:
    localizer = get_bot_localizer()
    status_key = (
        "bot.notifications.status.enabled" if enabled else "bot.notifications.status.disabled"
    )
    status = localizer.get(status_key)
    return localizer.format("bot.notifications.message", status=status)


def status_text(status: UserStatus) -> str:
    localizer = get_bot_localizer()
    mapping = {
        UserStatus.NONE: "bot.status.none",
        UserStatus.PROCESSING: "bot.status.processing",
        UserStatus.ATTENDEE: "bot.status.attendee",
        UserStatus.WAITLIST: "bot.status.waitlist",
    }
    return localizer.get(mapping[status])


def normalize_friend_username(value: str) -> str | None:
    normalized = value.lstrip("@").strip().lower()
    return normalized or None


def parse_friend_usernames(text: str) -> list[str]:
    if not text:
        return []
    tokens = re.split(r"[,\s]+", text)
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        normalized = normalize_friend_username(token)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def serialize_friend_usernames(usernames: list[str]) -> str | None:
    if not usernames:
        return None
    return ",".join(usernames)


def deserialize_friend_usernames(value: str | None) -> set[str]:
    if not value:
        return set()
    tokens = re.split(r"[,\s]+", value)
    usernames = {normalize_friend_username(token) for token in tokens}
    return {item for item in usernames if item}


async def send_welcome_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, template: MessageTemplate | None
) -> None:
    if not update.effective_chat:
        return
    if not template:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.templates.missing_welcome"))
        return
    try:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=template.admin_chat_id,
            message_id=template.message_id,
        )
    except Exception:
        logger.exception("Failed to send welcome template message")
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.templates.missing_welcome"))


async def send_schedule_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, template: MessageTemplate | None
) -> None:
    if not update.effective_chat:
        return
    if not template:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.templates.missing_schedule"))
        return
    try:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=template.admin_chat_id,
            message_id=template.message_id,
        )
    except Exception:
        logger.exception("Failed to send schedule template message")
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.templates.missing_schedule"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        template = get_template(session, "welcome_message")
        friend_matches: list[User] = []
        normalized_username = normalize_friend_username(user.username or "")
        if normalized_username:
            candidates = (
                session.execute(select(User).where(User.friend_usernames.is_not(None)))
                .scalars()
                .all()
            )
            for candidate in candidates:
                if candidate.telegram_id == user.id or candidate.status == UserStatus.NONE:
                    continue
                if normalized_username in deserialize_friend_usernames(
                    candidate.friend_usernames
                ):
                    friend_matches.append(candidate)

    localizer = get_bot_localizer()
    await send_welcome_message(update, context, template)
    for friend in friend_matches:
        if not update.effective_chat:
            return
        friend_label = (
            f"@{friend.username}"
            if friend.username
            else friend.full_name or localizer.get("bot.messages.friend_attending_unknown")
        )
        await update.effective_chat.send_message(
            localizer.format("bot.messages.friend_attending", friend=friend_label)
        )


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
        message = status_text(db_user.status)
    await context.bot.send_message(
        chat_id=chat.id,
        text=message,
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )


async def show_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
        message = notifications_text(db_user.notifications_enabled)
    await context.bot.send_message(
        chat_id=chat.id,
        text=message,
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )


async def notifications_disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        db_user.notifications_enabled = False
        event_state = get_or_create_event_state(session)
        message = notifications_text(db_user.notifications_enabled)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )


async def notifications_enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        db_user.notifications_enabled = True
        event_state = get_or_create_event_state(session)
        message = notifications_text(db_user.notifications_enabled)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )


async def application_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not update.effective_chat:
        return ConversationHandler.END
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
    localizer = get_bot_localizer()
    if db_user.status != UserStatus.NONE:
        await update.effective_chat.send_message(
            localizer.get("bot.application.already_created"),
            reply_markup=home_keyboard(),
        )
        return ConversationHandler.END
    await update.effective_chat.send_message(
        localizer.get("bot.application.ask_full_name"),
        reply_markup=home_keyboard(),
    )
    return APPLICATION_FULL_NAME


async def application_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return APPLICATION_FULL_NAME
    context.user_data["full_name"] = update.message.text.strip()
    localizer = get_bot_localizer()
    await update.message.reply_text(localizer.get("bot.application.ask_job"))
    return APPLICATION_JOB


async def application_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return APPLICATION_JOB
    context.user_data["job"] = update.message.text.strip()
    localizer = get_bot_localizer()
    await update.message.reply_text(localizer.get("bot.application.ask_career"))
    return APPLICATION_CAREER


async def application_career(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return APPLICATION_CAREER
    context.user_data["career_path"] = update.message.text.strip()
    localizer = get_bot_localizer()
    await update.message.reply_text(localizer.get("bot.application.ask_friends"))
    return APPLICATION_FRIENDS


async def application_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or update.message.text is None:
        return APPLICATION_FRIENDS
    friend_usernames = parse_friend_usernames(update.message.text)
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return ConversationHandler.END
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        db_user.full_name = context.user_data.get("full_name")
        db_user.job = context.user_data.get("job")
        db_user.career_path = context.user_data.get("career_path")
        db_user.friend_usernames = serialize_friend_usernames(friend_usernames)
        db_user.status = UserStatus.PROCESSING
        event_state = get_or_create_event_state(session)
    localizer = get_bot_localizer()
    await chat.send_message(
        localizer.get("bot.application.confirmation"),
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def application_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return ConversationHandler.END
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
    context.user_data.clear()
    localizer = get_bot_localizer()
    await chat.send_message(
        localizer.get("bot.messages.main_menu"),
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )
    return ConversationHandler.END


async def cancel_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        db_user.status = UserStatus.NONE
        db_user.full_name = None
        db_user.job = None
        db_user.career_path = None
        db_user.friend_usernames = None
        event_state = get_or_create_event_state(session)
    localizer = get_bot_localizer()
    await chat.send_message(
        localizer.get("bot.application.cancelled"),
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        template = get_template(session, "schedule_message")
    await send_schedule_message(update, context, template)


async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return ConversationHandler.END
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
    localizer = get_bot_localizer()
    if not (event_state.event_started and db_user.status != UserStatus.NONE):
        await chat.send_message(
            localizer.get("bot.messages.main_menu"),
            reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
        )
        return ConversationHandler.END
    await chat.send_message(
        localizer.get("bot.feedback.prompt"),
        reply_markup=home_keyboard(),
    )
    return FEEDBACK_TEXT


async def feedback_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return FEEDBACK_TEXT
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return ConversationHandler.END
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
        feedback = Feedback(
            event_id=event_state.current_event_id or "default",
            user_id=db_user.id,
            feedback_text=update.message.text.strip(),
            created_at=datetime.utcnow(),
        )
        session.add(feedback)
    localizer = get_bot_localizer()
    await chat.send_message(
        localizer.get("bot.feedback.confirmation"),
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )
    return ConversationHandler.END


async def feedback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await application_cancel(update, context)


async def send_attendee_notification(bot, telegram_id: int) -> None:
    await asyncio.sleep(30)
    with session_scope() as session:
        user = session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user or user.status != UserStatus.ATTENDEE or not user.notifications_enabled:
            return
    localizer = get_bot_localizer()
    await bot.send_message(
        chat_id=telegram_id,
        text=localizer.get("bot.status.attendee_notification"),
    )


async def handle_admin_payload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message or not is_admin(user.username):
        return
    with session_scope() as session:
        state = get_admin_state(session, user.id)
        if not state:
            return
        localizer = get_bot_localizer()
        if state.waiting_for == AdminStateType.UPLOAD_DB:
            if not update.message.document:
                if update.message.text and update.message.text.startswith("/"):
                    return
                await update.message.reply_text(localizer.get("bot.admin.errors.expected_csv"))
                return
            await process_upload_database(update, context, state.admin_id)
            await update.message.reply_text(localizer.get("bot.admin.database.updated"))
            return
        message = update.message
        if state.waiting_for == AdminStateType.WELCOME:
            if message.text and message.text.startswith("/"):
                await update.message.reply_text(localizer.get("bot.admin.templates.awaiting_welcome"))
                return
            set_template(session, "welcome_message", message.chat_id, message.message_id)
            clear_admin_state(session, user.id)
            await update.message.reply_text(localizer.get("bot.admin.templates.saved_welcome"))
            return
        if state.waiting_for == AdminStateType.SCHEDULE:
            if message.text and message.text.startswith("/"):
                await update.message.reply_text(
                    localizer.get("bot.admin.templates.awaiting_schedule")
                )
                return
            set_template(session, "schedule_message", message.chat_id, message.message_id)
            clear_admin_state(session, user.id)
            await update.message.reply_text(localizer.get("bot.admin.templates.saved_schedule"))
            return
        if state.waiting_for in (AdminStateType.BROADCAST_ALL, AdminStateType.BROADCAST_ATTENDEE):
            if message.text and message.text.startswith("/"):
                await update.message.reply_text(
                    localizer.get("bot.admin.broadcast.awaiting_message")
                )
                return
            clear_admin_state(session, user.id)
            await broadcast_payload(session, context, message, state.waiting_for)
            return


async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if not (user or chat or message):
        logger.info("Received update without user/chat/message (update_id=%s)", update.update_id)
        return
    logger.info(
        "Update received: update_id=%s user_id=%s username=%s "
        "chat_id=%s has_message=%s has_text=%s",
        update.update_id,
        user.id if user else None,
        user.username if user else None,
        chat.id if chat else None,
        bool(message),
        bool(message and message.text),
    )


async def broadcast_payload(session, context, message, waiting_for: AdminStateType) -> None:
    if waiting_for == AdminStateType.BROADCAST_ATTENDEE:
        users = (
            session.execute(
                select(User).where(
                    User.status == UserStatus.ATTENDEE,
                    User.notifications_enabled.is_(True),
                )
            )
            .scalars()
            .all()
        )
    else:
        users = (
            session.execute(select(User).where(User.notifications_enabled.is_(True)))
            .scalars()
            .all()
        )
    for target in users:
        try:
            await context.bot.copy_message(
                chat_id=target.telegram_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception:
            logger.exception("Failed to send broadcast to %s", target.telegram_id)


def ensure_admin(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.username):
        if update.effective_chat:
            asyncio.create_task(
                update.effective_chat.send_message(
                    get_bot_localizer().get("bot.admin.errors.unknown_or_forbidden")
                )
            )
        return False
    return True


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    commands = [
        "/admin",
        "/download_database",
        "/upload_database",
        "/check_applications",
        "/approve {nickname}",
        "/disapprove {nickname}",
        "/processing {nickname}",
        "/approve_id {user_id}",
        "/disapprove_id {user_id}",
        "/processing_id {user_id}",
        "/set_welcome_message",
        "/set_schedule_message",
        "/urgent_notification",
        "/urgent_notification_attendee",
        "/event_start",
        "/event_cancel",
        "/set_event_id {id}",
    ]
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(
        localizer.format("bot.admin.commands_list", commands="\n".join(commands))
    )


async def set_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        set_admin_state(session, user.id, AdminStateType.WELCOME)


async def set_schedule_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        set_admin_state(session, user.id, AdminStateType.SCHEDULE)


async def urgent_notification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        set_admin_state(session, user.id, AdminStateType.BROADCAST_ALL)
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(localizer.get("bot.admin.broadcast.awaiting_all"))


async def urgent_notification_attendee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        set_admin_state(session, user.id, AdminStateType.BROADCAST_ATTENDEE)
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(
        localizer.get("bot.admin.broadcast.awaiting_attendees")
    )


async def upload_database(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        set_admin_state(session, user.id, AdminStateType.UPLOAD_DB)
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(localizer.get("bot.admin.database.awaiting_upload"))


async def download_database(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    if not update.effective_chat:
        return
    with session_scope() as session:
        users = session.execute(select(User)).scalars().all()
        feedback = (
            session.execute(select(Feedback).options(selectinload(Feedback.user)))
            .scalars()
            .all()
        )
    user_stream = io.StringIO()
    user_writer = csv.writer(user_stream)
    user_writer.writerow(
        [
            "user_id",
            "username",
            "full_name",
            "job",
            "career_path",
            "friend_usernames",
            "status",
            "notifications_enabled",
            "created_at",
            "updated_at",
        ]
    )
    for user in users:
        user_writer.writerow(
            [
                user.telegram_id,
                user.username or "",
                user.full_name or "",
                user.job or "",
                user.career_path or "",
                user.friend_usernames or "",
                user.status.value,
                str(user.notifications_enabled).lower(),
                user.created_at.isoformat(),
                user.updated_at.isoformat() if user.updated_at else "",
            ]
        )
    user_stream.seek(0)
    await update.effective_chat.send_document(
        document=io.BytesIO(user_stream.getvalue().encode("utf-8")),
        filename="users.csv",
    )

    feedback_stream = io.StringIO()
    feedback_writer = csv.writer(feedback_stream)
    feedback_writer.writerow(["event_id", "user_id", "feedback_text", "created_at"])
    for item in feedback:
        feedback_writer.writerow(
            [
                item.event_id,
                item.user.telegram_id if item.user else "",
                item.feedback_text,
                item.created_at.isoformat(),
            ]
        )
    feedback_stream.seek(0)
    await update.effective_chat.send_document(
        document=io.BytesIO(feedback_stream.getvalue().encode("utf-8")),
        filename="feedback.csv",
    )


async def check_applications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    if not update.effective_chat:
        return
    with session_scope() as session:
        users = session.execute(select(User)).scalars().all()
    applications = [user for user in users if user.status != UserStatus.NONE]
    attendee_count = len([user for user in users if user.status == UserStatus.ATTENDEE])
    localizer = get_bot_localizer()
    lines = [
        localizer.format(
            "bot.admin.applications.summary",
            applications=len(applications),
            attendees=attendee_count,
        )
    ]
    for user in applications:
        label = f"@{user.username}" if user.username else str(user.telegram_id)
        lines.append(f"{label} -> {user.status.value}")
    await update.effective_chat.send_message("\n".join(lines))


def parse_username(text: str | None) -> str | None:
    if not text:
        return None
    return text.lstrip("@").strip()


async def update_status_by_username(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status: UserStatus
) -> None:
    if not ensure_admin(update):
        return
    if not update.effective_chat or not update.message:
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(
            localizer.get("bot.admin.errors.nickname_required")
        )
        return
    nickname = parse_username(parts[1])
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username.ilike(nickname)))
        if not user:
            localizer = get_bot_localizer()
            await update.effective_chat.send_message(
                localizer.get("bot.admin.errors.user_not_found")
            )
            return
        if user.status == UserStatus.NONE:
            localizer = get_bot_localizer()
            await update.effective_chat.send_message(
                localizer.get("bot.admin.errors.application_not_found")
            )
            return
        user.status = status
        user.updated_at = datetime.utcnow()
        if status == UserStatus.ATTENDEE:
            asyncio.create_task(send_attendee_notification(context.bot, user.telegram_id))
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(localizer.get("bot.admin.status.updated"))


async def update_status_by_id(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status: UserStatus
) -> None:
    if not ensure_admin(update):
        return
    if not update.effective_chat or not update.message:
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.admin.errors.user_id_required"))
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.admin.errors.user_id_invalid"))
        return
    with session_scope() as session:
        user = session.scalar(select(User).where(User.telegram_id == user_id))
        if not user:
            localizer = get_bot_localizer()
            await update.effective_chat.send_message(
                localizer.get("bot.admin.errors.user_not_found")
            )
            return
        if user.status == UserStatus.NONE:
            localizer = get_bot_localizer()
            await update.effective_chat.send_message(
                localizer.get("bot.admin.errors.application_not_found")
            )
            return
        user.status = status
        user.updated_at = datetime.utcnow()
        if status == UserStatus.ATTENDEE:
            asyncio.create_task(send_attendee_notification(context.bot, user.telegram_id))
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(localizer.get("bot.admin.status.updated"))


async def event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    with session_scope() as session:
        state = get_or_create_event_state(session)
        if state.event_started:
            localizer = get_bot_localizer()
            if update.effective_chat:
                await update.effective_chat.send_message(
                    localizer.get("bot.admin.event_already_started")
                )
            return
        state.event_started = True
        users = session.execute(select(User)).scalars().all()
    localizer = get_bot_localizer()
    if update.effective_chat:
        await update.effective_chat.send_message(localizer.get("bot.admin.event_started"))
    for user in users:
        if not user.telegram_id:
            continue
        try:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text=localizer.get("bot.messages.event_started_broadcast"),
                reply_markup=build_main_keyboard(user.status, True),
            )
        except Exception:
            logger.exception("Failed to send event started broadcast to %s", user.telegram_id)


async def event_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    with session_scope() as session:
        state = get_or_create_event_state(session)
        state.event_started = False
        users = session.execute(select(User)).scalars().all()
    localizer = get_bot_localizer()
    if update.effective_chat:
        for user in users:
            if not user.telegram_id:
                continue
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=localizer.get("bot.admin.event_cancelled"),
                    reply_markup=build_main_keyboard(user.status, False),
                )
            except Exception:
                logger.exception("Failed to send event cancelled broadcast to %s", user.telegram_id)


async def set_event_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    if not update.message or not update.effective_chat:
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(
            localizer.get("bot.admin.errors.event_id_required")
        )
        return
    with session_scope() as session:
        state = get_or_create_event_state(session)
        state.current_event_id = parts[1]
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(localizer.get("bot.admin.event_id_updated"))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    if not is_admin(user.username):
        await update.effective_chat.send_message(
            get_bot_localizer().get("bot.admin.errors.unknown_or_forbidden")
        )
    else:
        await update.effective_chat.send_message(
            get_bot_localizer().get("bot.admin.errors.unknown")
        )


def register(application: Application) -> None:
    application.add_handler(MessageHandler(filters.ALL, handle_admin_payload), group=1)
    application.add_handler(MessageHandler(filters.ALL, log_update), group=2)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("notifications_disable", notifications_disable))
    application.add_handler(CommandHandler("notifications_enable", notifications_enable))

    application.add_handler(CommandHandler("admin", admin_help))
    application.add_handler(CommandHandler("download_database", download_database))
    application.add_handler(CommandHandler("upload_database", upload_database))
    application.add_handler(CommandHandler("check_applications", check_applications))
    application.add_handler(CommandHandler("set_welcome_message", set_welcome_message))
    application.add_handler(CommandHandler("set_schedule_message", set_schedule_message))
    application.add_handler(CommandHandler("urgent_notification", urgent_notification))
    application.add_handler(
        CommandHandler("urgent_notification_attendee", urgent_notification_attendee)
    )
    application.add_handler(CommandHandler("event_start", event_start))
    application.add_handler(CommandHandler("event_cancel", event_cancel))
    application.add_handler(CommandHandler("set_event_id", set_event_id))
    application.add_handler(
        CommandHandler(
            "approve",
            lambda update, context: update_status_by_username(update, context, UserStatus.ATTENDEE),
        )
    )
    application.add_handler(
        CommandHandler(
            "disapprove",
            lambda update, context: update_status_by_username(update, context, UserStatus.WAITLIST),
        )
    )
    application.add_handler(
        CommandHandler(
            "processing",
            lambda update, context: update_status_by_username(
                update, context, UserStatus.PROCESSING
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "approve_id",
            lambda update, context: update_status_by_id(update, context, UserStatus.ATTENDEE),
        )
    )
    application.add_handler(
        CommandHandler(
            "disapprove_id",
            lambda update, context: update_status_by_id(update, context, UserStatus.WAITLIST),
        )
    )
    application.add_handler(
        CommandHandler(
            "processing_id",
            lambda update, context: update_status_by_id(update, context, UserStatus.PROCESSING),
        )
    )

    application.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(f"^{MENU_APPLICATION}$"), application_start)
            ],
            states={
                APPLICATION_FULL_NAME: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{MENU_HOME}$"),
                        application_full_name,
                    )
                ],
                APPLICATION_JOB: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{MENU_HOME}$"),
                        application_job,
                    )
                ],
                APPLICATION_CAREER: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{MENU_HOME}$"),
                        application_career,
                    )
                ],
                APPLICATION_FRIENDS: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{MENU_HOME}$"),
                        application_friends,
                    )
                ],
            },
            fallbacks=[MessageHandler(filters.Regex(f"^{MENU_HOME}$"), application_cancel)],
        )
    )

    application.add_handler(
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(f"^{MENU_FEEDBACK}$"), feedback_start)],
            states={
                FEEDBACK_TEXT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{MENU_HOME}$"),
                        feedback_save,
                    )
                ]
            },
            fallbacks=[MessageHandler(filters.Regex(f"^{MENU_HOME}$"), feedback_cancel)],
        )
    )

    application.add_handler(MessageHandler(filters.Regex(f"^{MENU_CANCEL}$"), cancel_application))
    application.add_handler(MessageHandler(filters.Regex(f"^{MENU_SCHEDULE}$"), schedule))
    application.add_handler(MessageHandler(filters.Regex(f"^{MENU_STATUS}$"), show_status))
    application.add_handler(
        MessageHandler(filters.Regex(f"^{MENU_NOTIFICATIONS}$"), show_notifications)
    )

    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
