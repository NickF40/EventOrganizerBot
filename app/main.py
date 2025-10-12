import asyncio
import uvicorn

from app.config import get_settings
from app.database import Base, engine
from app.scheduler import start_scheduler, stop_scheduler
from app.telegram.bot import build_application
from app.web.admin import create_app


async def run() -> None:
    settings = get_settings()

    Base.metadata.create_all(bind=engine)

    from app.database import session_scope
    from app.services.events import get_or_create_default_event

    with session_scope() as session:
        get_or_create_default_event(session, settings)
    application = build_application(settings)
    scheduler = start_scheduler(settings, application.bot)
    admin_app = create_app(settings, bot=application.bot)

    config = uvicorn.Config(
        admin_app, host=settings.web_host, port=settings.web_port, log_level="info"
    )
    server = uvicorn.Server(config)

    await application.initialize()
    if application.post_init:
        await application.post_init(application)
    await application.updater.start_polling()
    await application.start()

    try:
        await server.serve()
    finally:
        stop_scheduler(scheduler)
        if application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
            if application.post_stop:
                await application.post_stop(application)
        await application.shutdown()
        if application.post_shutdown:
            await application.post_shutdown(application)
        if not server.should_exit:
            server.should_exit = True


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
