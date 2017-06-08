from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Bot
from telegram.error import TelegramError

from app.models import ScheduledPost, User


def schedule_post(
    session: Session, *, title: str, content: str, send_at: datetime
) -> ScheduledPost:
    post = ScheduledPost(title=title, content=content, send_at=send_at)
    session.add(post)
    session.flush()
    return post


def get_pending_posts(session: Session, *, now: datetime | None = None) -> list[ScheduledPost]:
    now = now or datetime.now(timezone.utc)
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
    users = session.execute(select(User).where(User.is_subscribed.is_(True))).scalars().all()
    for user in users:
        if user.telegram_id is None:
            continue
        try:
            await bot.send_message(chat_id=user.telegram_id, text=f"{post.title}\n\n{post.content}")
        except TelegramError:
            user.is_subscribed = False
    post.sent_at = datetime.now(timezone.utc)
