import logging

from telegram.ext import Application, ApplicationBuilder

from app.config import get_settings
from app.telebot import handlers


logger = logging.getLogger(__name__)


def build_application() -> Application:
    settings = get_settings()
    logger.info("Building Telegram application. Token configured: %s", bool(settings.telegram_token))
    application = ApplicationBuilder().token(settings.telegram_token).build()
    handlers.register(application)
    return application
