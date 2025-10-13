from telegram.ext import ApplicationBuilder, Application

from app.config import Settings
from app.telebot import handlers


def build_application(settings: Settings) -> Application:
    application = ApplicationBuilder().token(settings.telegram_token).build()
    handlers.register(application)
    return application
