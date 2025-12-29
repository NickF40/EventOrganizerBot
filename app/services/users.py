from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import User as TGUser

from app.models import User


def resolve_display_name(tg_user: TGUser) -> str:
    if tg_user.full_name:
        return tg_user.full_name
    if tg_user.username:
        return tg_user.username
    return str(tg_user.id)


def get_or_create_user(session: Session, tg_user: TGUser) -> User:
    user = session.scalar(select(User).where(User.telegram_id == tg_user.id))
    if user:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.display_name = resolve_display_name(tg_user)
        user.contact = tg_user.username
        user.is_subscribed = True
        return user

    user = User(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        display_name=resolve_display_name(tg_user),
        contact=tg_user.username,
        is_manual=False,
    )
    session.add(user)
    session.flush()
    return user
