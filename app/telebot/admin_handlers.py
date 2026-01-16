import asyncio
import csv
import io
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import Update
from telegram.ext import ContextTypes

from app.database import session_scope
from app.models import AdminStateType, Feedback, User, UserStatus
from app.telebot.common import get_bot_localizer
from app.telebot.db import (
    clear_admin_state,
    get_admin_state,
    get_or_create_event_state,
    is_admin,
    process_upload_database,
    set_admin_state,
    set_template,
)
from app.telebot.user_handlers import broadcast_text as user_broadcast_text

logger = logging.getLogger(__name__)


async def handle_admin_payload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message or not is_admin(user.username):
        return
    with session_scope() as session:
        state = get_admin_state(session, user.id)
        if not state:
            logger.debug("Admin payload ignored: no state for admin_id=%s", user.id)
            return
        localizer = get_bot_localizer()
        if state.waiting_for == AdminStateType.UPLOAD_DB:
            if not update.message.document:
                if update.message.text and update.message.text.startswith("/"):
                    return
                await update.message.reply_text(localizer.get("bot.admin.errors.expected_csv"))
                logger.info("Admin upload rejected: expected document admin_id=%s", user.id)
                return
            logger.info("Admin upload received admin_id=%s", user.id)
            await process_upload_database(update, context, state.admin_id)
            await update.message.reply_text(localizer.get("bot.admin.database.updated"))
            return
        message = update.message
        if state.waiting_for == AdminStateType.WELCOME:
            if message.text and message.text.startswith("/"):
                await update.message.reply_text(
                    localizer.get("bot.admin.templates.awaiting_welcome")
                )
                return
            set_template(session, "welcome_message", message.chat_id, message.message_id)
            clear_admin_state(session, user.id)
            logger.info("Admin welcome template saved admin_id=%s", user.id)
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
            logger.info("Admin schedule template saved admin_id=%s", user.id)
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
    logger.info("Broadcasting admin message to %s users", len(users))
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
    logger.info("Admin requested welcome template admin_id=%s", user.id)


async def set_schedule_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        set_admin_state(session, user.id, AdminStateType.SCHEDULE)
    logger.info("Admin requested schedule template admin_id=%s", user.id)


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
    logger.info("Admin requested urgent broadcast to all admin_id=%s", user.id)


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
    logger.info("Admin requested urgent broadcast to attendees admin_id=%s", user.id)


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
    logger.info("Admin requested database upload admin_id=%s", user.id)


async def download_database(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    if not update.effective_chat:
        return
    with session_scope() as session:
        users = session.execute(select(User)).scalars().all()
        feedback = (
            session.execute(select(Feedback).options(selectinload(Feedback.user))).scalars().all()
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
    logger.info("Admin database download completed users=%s feedback=%s", len(users), len(feedback))


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
    logger.info(
        "Admin application list sent admin_id=%s applications=%s",
        update.effective_user.id if update.effective_user else None,
        len(applications),
    )


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
    logger.info(
        "Admin status updated by username admin_id=%s username=%s status=%s",
        update.effective_user.id if update.effective_user else None,
        nickname,
        status.value,
    )


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
        user.status = status
        user.updated_at = datetime.utcnow()
        if status == UserStatus.ATTENDEE:
            asyncio.create_task(send_attendee_notification(context.bot, user.telegram_id))
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(localizer.get("bot.admin.status.updated"))
    logger.info(
        "Admin status updated by user_id admin_id=%s user_id=%s status=%s",
        update.effective_user.id if update.effective_user else None,
        user_id,
        status.value,
    )


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
            logger.info(
                "Admin event start ignored (already started) admin_id=%s",
                update.effective_user.id if update.effective_user else None,
            )
            return
        state.event_started = True
    localizer = get_bot_localizer()
    if update.effective_chat:
        await update.effective_chat.send_message(localizer.get("bot.admin.event_started"))
    await _broadcast_text(context.bot, localizer.get("bot.messages.event_started_broadcast"))
    logger.info(
        "Admin event started admin_id=%s",
        update.effective_user.id if update.effective_user else None,
    )


async def event_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ensure_admin(update):
        return
    with session_scope() as session:
        state = get_or_create_event_state(session)
        state.event_started = False
    localizer = get_bot_localizer()
    if update.effective_chat:
        await _broadcast_text(context.bot, localizer.get("bot.admin.event_cancelled"))
    logger.info(
        "Admin event cancelled admin_id=%s",
        update.effective_user.id if update.effective_user else None,
    )


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
    logger.info(
        "Admin event id updated admin_id=%s event_id=%s",
        update.effective_user.id if update.effective_user else None,
        parts[1],
    )


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
    logger.info("Attendee notification sent telegram_id=%s", telegram_id)


async def _broadcast_text(bot, text: str) -> None:
    try:
        from app.telebot import handlers as handlers_module

        broadcast = getattr(handlers_module, "broadcast_text", None)
    except Exception:
        broadcast = None
    await (broadcast or user_broadcast_text)(bot, text)
