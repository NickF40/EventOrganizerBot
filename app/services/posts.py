import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Bot
from telegram.error import TelegramError

from app.models import ScheduledPost, User

logger = logging.getLogger(__name__)


def schedule_post(
    session: Session, *, title: str, content: str, send_at: datetime
) -> ScheduledPost:
    if send_at.tzinfo is None:
        logger.warning("Scheduling post without timezone information; assuming UTC.")
        send_at = send_at.replace(tzinfo=timezone.utc)

    normalized = send_at.astimezone(timezone.utc)
    post = ScheduledPost(title=title, content=content, send_at=normalized)
    session.add(post)
    session.flush()
    logger.info(
        "Scheduled post %s ('%s') to be sent at %s (UTC)",
        post.id,
        post.title,
        normalized.isoformat(),
    )
    return post


def get_pending_posts(session: Session, *, now: datetime | None = None) -> list[ScheduledPost]:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (
        session.execute(
            select(ScheduledPost)
            .where(ScheduledPost.sent_at.is_(None))
            .where(ScheduledPost.send_at <= now)
            .order_by(ScheduledPost.send_at.asc())
        )
        .scalars()
        .all()
    )


async def broadcast_post(session: Session, bot: Bot, post: ScheduledPost) -> None:
    users = (
        session.execute(select(User).where(User.notifications_enabled.is_(True))).scalars().all()
    )
    logger.info("Broadcasting post %s to %s subscribed users", post.id, len(users))
    for user in users:
        if user.telegram_id is None:
            continue
        try:
            await bot.send_message(chat_id=user.telegram_id, text=f"{post.title}\n\n{post.content}")
        except TelegramError:
            user.notifications_enabled = False
            logger.warning(
                "Failed to deliver post %s to user %s; unsubscribing.",
                post.id,
                user.id,
            )
        else:
            logger.debug("Delivered post %s to user %s", post.id, user.id)
    post.sent_at = datetime.now(timezone.utc)
    logger.info("Post %s marked as sent at %s", post.id, post.sent_at.isoformat())
