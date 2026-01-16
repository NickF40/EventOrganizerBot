import logging
import re

from telegram import ReplyKeyboardMarkup

from app.config import get_settings
from app.localization import DEFAULT_LOCALE, get_localizer
from app.models import UserStatus

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
