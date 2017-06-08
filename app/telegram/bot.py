from telegram.ext import Application

from app.config import Settings
from app.telegram import handlers


def build_application(settings: Settings) -> Application:
    application = Application.builder().token(settings.telegram_token).build()
    handlers.register(application)
    return application
