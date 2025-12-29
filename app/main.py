import asyncio
import logging
import os

from uvicorn import Config, Server

from app.config import get_settings
from app.database import ensure_schema
from app.telebot.bot import build_application
from app.utils import config_path
from app.web.admin import create_app


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def run() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    settings = get_settings()
    logger.info("Config file: %s", config_path() or "not found")
    logger.info("Admin usernames loaded: %s", settings.admin_usernames)
    logger.info("Database URL: %s", settings.database_url)
    ensure_schema()

    application = build_application()
    admin_server: Server | None = None
    admin_task: asyncio.Task | None = None
    if settings.enable_admin_web:
        admin_app = create_app(settings, bot=application.bot)
        admin_server = Server(
            Config(
                admin_app,
                host=settings.admin_web_host,
                port=settings.admin_web_port,
                loop="asyncio",
                log_level="info",
            )
        )
        admin_task = asyncio.create_task(admin_server.serve())
        logger.info(
            "Admin web enabled on http://%s:%s", settings.admin_web_host, settings.admin_web_port
        )
    logger.info("Telegram application initialized; starting polling.")
    await application.initialize()
    if application.post_init:
        await application.post_init(application)
    await application.updater.start_polling()
    await application.start()

    try:
        await asyncio.Event().wait()
    finally:
        if admin_server and admin_task:
            admin_server.should_exit = True
            await admin_task
        if application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
            if application.post_stop:
                await application.post_stop(application)
        await application.shutdown()
        if application.post_shutdown:
            await application.post_shutdown(application)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
