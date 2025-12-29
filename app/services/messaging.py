from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Bot
from telegram.error import TelegramError

from app.models import User


async def broadcast_message(session: Session, bot: Bot, message: str) -> int:
    delivered = 0
    users = (
        session.execute(select(User).where(User.notifications_enabled.is_(True)))
        .scalars()
        .all()
    )
    for user in users:
        if user.telegram_id is None:
            continue
        try:
            await bot.send_message(chat_id=user.telegram_id, text=message)
            delivered += 1
        except TelegramError:
            user.notifications_enabled = False
    return delivered
