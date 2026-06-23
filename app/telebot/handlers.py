import logging

from telegram import Update
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
from app.models import UserStatus
from app.telebot.admin_handlers import (
    admin_help,
    broadcast_payload,
    check_applications,
    download_database,
    ensure_admin,
    event_cancel,
    event_start,
    handle_admin_payload,
    parse_username,
    send_attendee_notification,
    set_event_id,
    set_schedule_message,
    set_welcome_message,
    update_status_by_id,
    update_status_by_username,
    upload_database,
    urgent_notification,
    urgent_notification_attendee,
)
from app.telebot.common import (
    MENU_APPLICATION,
    MENU_CANCEL,
    MENU_FEEDBACK,
    MENU_HOME,
    MENU_NOTIFICATIONS,
    MENU_SCHEDULE,
    MENU_STATUS,
    build_main_keyboard,
    deserialize_friend_usernames,
    get_bot_localizer,
    normalize_friend_username,
    notifications_text,
    parse_friend_usernames,
    serialize_friend_usernames,
    status_text,
)
from app.telebot.db import (
    get_or_create_event_state,
    is_admin,
    process_upload_database,
    set_admin_state,
    upsert_user,
)
from app.telebot.user_handlers import (
    APPLICATION_CAREER,
    APPLICATION_FRIENDS,
    APPLICATION_FULL_NAME,
    APPLICATION_JOB,
    FEEDBACK_TEXT,
    application_cancel,
    application_career,
    application_friends,
    application_full_name,
    application_job,
    application_start,
    broadcast_text,
    cancel_application,
    feedback_cancel,
    feedback_save,
    feedback_start,
    go_home,
    notifications_disable,
    notifications_enable,
    schedule,
    send_schedule_message,
    send_welcome_message,
    show_notifications,
    show_status,
    start,
)

logger = logging.getLogger(__name__)

__all__ = [
    "APPLICATION_CAREER",
    "APPLICATION_FRIENDS",
    "APPLICATION_FULL_NAME",
    "APPLICATION_JOB",
    "FEEDBACK_TEXT",
    "MENU_APPLICATION",
    "MENU_CANCEL",
    "MENU_FEEDBACK",
    "MENU_HOME",
    "MENU_NOTIFICATIONS",
    "MENU_SCHEDULE",
    "MENU_STATUS",
    "admin_help",
    "application_cancel",
    "application_career",
    "application_friends",
    "application_full_name",
    "application_job",
    "application_start",
    "broadcast_payload",
    "broadcast_text",
    "build_main_keyboard",
    "cancel_application",
    "check_applications",
    "deserialize_friend_usernames",
    "download_database",
    "ensure_admin",
    "event_cancel",
    "event_start",
    "feedback_cancel",
    "feedback_save",
    "feedback_start",
    "get_bot_localizer",
    "get_settings",
    "handle_admin_payload",
    "is_admin",
    "log_update",
    "normalize_friend_username",
    "notifications_disable",
    "notifications_enable",
    "notifications_text",
    "parse_friend_usernames",
    "parse_username",
    "process_upload_database",
    "register",
    "schedule",
    "send_attendee_notification",
    "send_schedule_message",
    "send_welcome_message",
    "serialize_friend_usernames",
    "set_admin_state",
    "set_event_id",
    "set_schedule_message",
    "set_welcome_message",
    "show_notifications",
    "show_status",
    "start",
    "status_text",
    "unknown_command",
    "update_status_by_id",
    "update_status_by_username",
    "upload_database",
    "urgent_notification",
    "urgent_notification_attendee",
]


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


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_chat:
        return
    if not is_admin(user.username):
        with session_scope() as session:
            db_user, _ = upsert_user(session, user)
            event_state = get_or_create_event_state(session)
        await update.effective_chat.send_message(
            get_bot_localizer().get("bot.admin.errors.unknown_or_forbidden"),
            reply_markup=build_main_keyboard(db_user.status, event_state.event_started),
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
    application.add_handler(MessageHandler(filters.Regex(f"^{MENU_HOME}$"), go_home))

    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(MessageHandler(filters.ALL, unknown_command))
