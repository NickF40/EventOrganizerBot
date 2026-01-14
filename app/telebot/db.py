import csv
import io
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.config import get_settings
from app.database import session_scope
from app.models import (
    AdminState,
    AdminStateType,
    EventState,
    Feedback,
    MessageTemplate,
    User,
    UserStatus,
)

logger = logging.getLogger(__name__)


def is_admin(username: str | None) -> bool:
    if not username:
        return False
    settings = get_settings()
    return username.lstrip("@").lower() in settings.admin_username_set


def get_or_create_event_state(session) -> EventState:
    state = session.scalar(select(EventState))
    if state:
        return state
    state = EventState(event_started=False, current_event_id="default")
    session.add(state)
    session.flush()
    return state


def upsert_user(session, tg_user) -> tuple[User, bool]:
    user = session.scalar(select(User).where(User.telegram_id == tg_user.id))
    is_new = False
    if not user:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            status=UserStatus.NONE,
            notifications_enabled=True,
            created_at=datetime.utcnow(),
        )
        session.add(user)
        session.flush()
        is_new = True
    user.username = tg_user.username
    user.updated_at = datetime.utcnow()
    return user, is_new


def get_template(session, name: str) -> MessageTemplate | None:
    return session.scalar(select(MessageTemplate).where(MessageTemplate.name == name))


def set_template(session, name: str, chat_id: int, message_id: int) -> None:
    template = get_template(session, name)
    if template:
        template.admin_chat_id = chat_id
        template.message_id = message_id
        return
    template = MessageTemplate(name=name, admin_chat_id=chat_id, message_id=message_id)
    session.add(template)


def set_admin_state(
    session, admin_id: int, waiting_for: AdminStateType, ttl_seconds: int = 300
) -> None:
    session.query(AdminState).where(AdminState.admin_id == admin_id).delete()
    state = AdminState(
        admin_id=admin_id,
        waiting_for=waiting_for,
        ttl_seconds=ttl_seconds,
        created_at=datetime.utcnow(),
    )
    session.add(state)


def clear_admin_state(session, admin_id: int) -> None:
    session.query(AdminState).where(AdminState.admin_id == admin_id).delete()


def get_admin_state(session, admin_id: int) -> AdminState | None:
    state = session.scalar(select(AdminState).where(AdminState.admin_id == admin_id))
    if not state:
        return None
    if datetime.utcnow() > state.created_at + timedelta(seconds=state.ttl_seconds):
        session.delete(state)
        return None
    return state


def _parse_user_id(value: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)
    try:
        float_value = float(stripped)
    except ValueError:
        return None
    if float_value.is_integer():
        return int(float_value)
    return None


def _normalize_key(key: str) -> str:
    return key.lstrip("\ufeff").strip()


def _parse_status(value: str | None) -> UserStatus | None:
    if not value:
        return None
    normalized_status = value.strip().upper()
    if normalized_status in UserStatus.__members__:
        return UserStatus[normalized_status]
    for candidate in UserStatus:
        if candidate.value.upper() == normalized_status:
            return candidate
    return None


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _build_user_from_row(row: dict[str, str]) -> User | None:
    user_id_value = row.get("user_id")
    if not user_id_value:
        logger.info("Skipping row without user_id: %s", row)
        return None
    telegram_id = _parse_user_id(str(user_id_value))
    if telegram_id is None:
        logger.warning("Skipping row with invalid user_id=%s", user_id_value)
        return None
    username = row.get("username") or None
    if username and is_admin(username) and not get_settings().allow_admin_upload_overwrite:
        logger.info("Skipping admin row for username=%s user_id=%s", username, telegram_id)
        return None
    status = _parse_status(row.get("status"))
    if row.get("status") and status is None:
        logger.warning(
            "Skipping row with invalid status=%s for user_id=%s",
            row.get("status"),
            telegram_id,
        )
        return None
    notifications_enabled = _parse_bool(row.get("notifications_enabled"))
    return User(
        telegram_id=telegram_id,
        username=username,
        full_name=row.get("full_name") or None,
        job=row.get("job") or None,
        career_path=row.get("career_path") or None,
        status=status or UserStatus.NONE,
        notifications_enabled=notifications_enabled
        if notifications_enabled is not None
        else True,
    )


def _normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized_key = _normalize_key(key)
        if isinstance(value, str):
            normalized_value = value.strip()
        else:
            normalized_value = value
        normalized[normalized_key] = normalized_value
    return normalized


def _validate_schema(fieldnames: list[str] | None) -> bool:
    required_fields = {
        "user_id",
        "username",
        "full_name",
        "job",
        "career_path",
        "status",
        "notifications_enabled",
    }
    if not fieldnames:
        logger.error("Uploaded CSV has no headers")
        return False
    normalized_fields = {_normalize_key(field) for field in fieldnames}
    missing_fields = required_fields - normalized_fields
    if missing_fields:
        logger.error("Uploaded CSV missing required columns: %s", sorted(missing_fields))
        return False
    return True


async def process_upload_database(
    update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id: int
) -> None:
    if not update.message or not update.message.document:
        return
    document_name = getattr(update.message.document, "file_name", None)
    logger.info(
        "Processing uploaded database from admin_id=%s document_name=%s",
        admin_id,
        document_name,
    )
    file = await context.bot.get_file(update.message.document.file_id)
    content = await file.download_as_bytearray()
    text = content.decode("utf-8")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    logger.info(
        "Detected CSV dialect for upload: delimiter=%r, headers=%s",
        getattr(dialect, "delimiter", None),
        reader.fieldnames,
    )
    if not _validate_schema(reader.fieldnames):
        logger.warning("Aborting upload due to invalid schema")
        return

    with session_scope() as session:
        clear_admin_state(session, admin_id)
        total_rows = 0
        skipped_rows = 0
        inserted_rows = 0
        users_to_insert: list[User] = []
        for row in reader:
            total_rows += 1
            normalized_row = _normalize_row(row)
            logger.debug("Processing CSV row=%s", normalized_row)
            csv_user = _build_user_from_row(normalized_row)
            if not csv_user:
                skipped_rows += 1
                continue
            users_to_insert.append(csv_user)
        if not users_to_insert:
            logger.warning(
                "No users parsed from upload; skipping delete to avoid empty reload"
            )
            return
        logger.info("Deleting existing feedback and users before upload")
        session.query(Feedback).delete()
        session.query(User).delete()
        for user in users_to_insert:
            user.created_at = datetime.utcnow()
            user.updated_at = datetime.utcnow()
            session.add(user)
            inserted_rows += 1
        logger.info(
            "Upload database summary: total_rows=%s inserted_rows=%s skipped_rows=%s",
            total_rows,
            inserted_rows,
            skipped_rows,
        )
