import logging
from datetime import datetime

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from app.database import session_scope
from app.models import EventState, Feedback, MessageTemplate, User, UserStatus
from app.telebot.common import (
    build_main_keyboard,
    deserialize_friend_usernames,
    get_bot_localizer,
    home_keyboard,
    notifications_text,
    parse_friend_usernames,
    serialize_friend_usernames,
    status_text,
)
from app.telebot.db import get_or_create_event_state, get_template, upsert_user

logger = logging.getLogger(__name__)

APPLICATION_FULL_NAME = 1
APPLICATION_JOB = 2
APPLICATION_CAREER = 3
APPLICATION_FRIENDS = 4

FEEDBACK_TEXT = 10


async def broadcast_text(bot, text: str) -> None:
    with session_scope() as session:
        users = session.execute(select(User)).scalars().all()
        event_state = get_or_create_event_state(session)
        event_started = event_state.event_started
    logger.info("Broadcasting text message to %s users", len(users))
    for user in users:
        if not user.telegram_id:
            continue
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                reply_markup=build_main_keyboard(user.status, event_started),
            )
        except Exception:
            logger.exception("Failed to broadcast message to %s", user.telegram_id)


async def send_welcome_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    template: MessageTemplate | None,
    reply_markup=None,
) -> None:
    if not update.effective_chat:
        return
    if not template:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(
            localizer.get("bot.templates.missing_welcome"),
            reply_markup=reply_markup,
        )
        logger.warning("Welcome template missing for chat_id=%s", update.effective_chat.id)
        return
    try:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=template.admin_chat_id,
            message_id=template.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("Failed to send welcome template message")
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(
            localizer.get("bot.templates.missing_welcome"),
            reply_markup=reply_markup,
        )


async def send_schedule_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, template: MessageTemplate | None
) -> None:
    if not update.effective_chat:
        return
    if not template:
        localizer = get_bot_localizer()
        await update.effective_chat.send_message(localizer.get("bot.templates.missing_schedule"))
        logger.warning("Schedule template missing for chat_id=%s", update.effective_chat.id)
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


def _persist_application(
    session,
    user,
    context: ContextTypes.DEFAULT_TYPE,
    friend_usernames: list[str],
) -> tuple[User, EventState]:
    db_user, is_new = upsert_user(session, user)
    db_user.full_name = context.user_data.get("full_name")
    db_user.job = context.user_data.get("job")
    db_user.career_path = context.user_data.get("career_path")
    db_user.friend_usernames = serialize_friend_usernames(friend_usernames)
    db_user.status = UserStatus.PROCESSING
    event_state = get_or_create_event_state(session)
    logger.info(
        "Application saved user_id=%s new_user=%s friend_count=%s",
        db_user.telegram_id,
        is_new,
        len(friend_usernames),
    )
    return db_user, event_state


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    with session_scope() as session:
        db_user, is_new = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
        template = get_template(session, "welcome_message")
        friend_matches: list[User] = []
        normalized_username = user.username or ""
        normalized_username = normalized_username.lstrip("@").strip().lower()
        if normalized_username:
            candidates = (
                session.execute(select(User).where(User.friend_usernames.is_not(None)))
                .scalars()
                .all()
            )
            for candidate in candidates:
                if candidate.telegram_id == user.id or candidate.status == UserStatus.NONE:
                    continue
                if normalized_username in deserialize_friend_usernames(candidate.friend_usernames):
                    friend_matches.append(candidate)

    logger.info(
        "Start handled user_id=%s new_user=%s friend_matches=%s",
        db_user.telegram_id,
        is_new,
        len(friend_matches),
    )
    localizer = get_bot_localizer()
    await send_welcome_message(
        update,
        context,
        template,
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )
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
    logger.debug("Status shown user_id=%s status=%s", user.id, db_user.status.value)


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
    logger.debug(
        "Notifications status shown user_id=%s enabled=%s",
        user.id,
        db_user.notifications_enabled,
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
    logger.info("Notifications disabled user_id=%s", user.id)


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
    logger.info("Notifications enabled user_id=%s", user.id)


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
    localizer = get_bot_localizer()
    await update.effective_chat.send_message(
        localizer.get("bot.messages.main_menu"),
        reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
    )


async def application_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not update.effective_chat:
        return ConversationHandler.END
    with session_scope() as session:
        db_user, _ = upsert_user(session, user)
        event_state = get_or_create_event_state(session)
    localizer = get_bot_localizer()
    if db_user.status != UserStatus.NONE:
        await update.effective_chat.send_message(
            localizer.get("bot.application.already_created"),
            reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
        )
        return ConversationHandler.END
    await update.effective_chat.send_message(
        localizer.get("bot.application.ask_full_name"),
        reply_markup=home_keyboard(),
    )
    logger.info("Application flow started user_id=%s", user.id)
    return APPLICATION_FULL_NAME


async def application_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return APPLICATION_FULL_NAME
    context.user_data["full_name"] = update.message.text.strip()
    localizer = get_bot_localizer()
    await update.message.reply_text(localizer.get("bot.application.ask_job"))
    logger.debug("Application full name received user_id=%s", update.effective_user.id)
    return APPLICATION_JOB


async def application_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return APPLICATION_JOB
    context.user_data["job"] = update.message.text.strip()
    localizer = get_bot_localizer()
    await update.message.reply_text(localizer.get("bot.application.ask_career"))
    logger.debug("Application job received user_id=%s", update.effective_user.id)
    return APPLICATION_CAREER


async def application_career(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return APPLICATION_CAREER
    context.user_data["career_path"] = update.message.text.strip()
    localizer = get_bot_localizer()
    await update.message.reply_text(localizer.get("bot.application.ask_friends"))
    logger.debug("Application career path received user_id=%s", update.effective_user.id)
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
        db_user, event_state = _persist_application(session, user, context, friend_usernames)
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
    logger.info("Application flow cancelled user_id=%s", user.id)
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
    logger.info("Application cancelled user_id=%s", user.id)


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    with session_scope() as session:
        template = get_template(session, "schedule_message")
    await send_schedule_message(update, context, template)
    logger.info("Schedule requested user_id=%s", user.id)


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
        logger.info("Feedback blocked user_id=%s status=%s", user.id, db_user.status.value)
        return ConversationHandler.END
    await chat.send_message(
        localizer.get("bot.feedback.prompt"),
        reply_markup=home_keyboard(),
    )
    logger.info("Feedback flow started user_id=%s", user.id)
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
    logger.info("Feedback saved user_id=%s event_id=%s", user.id, feedback.event_id)
    return ConversationHandler.END


async def feedback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await application_cancel(update, context)
