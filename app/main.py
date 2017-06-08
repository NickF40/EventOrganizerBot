import asyncio
from contextlib import suppress

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

    bot_task = asyncio.create_task(application.run_polling(close_loop=False))
    server_task = asyncio.create_task(server.serve())

    try:
        await asyncio.wait({bot_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_scheduler(scheduler)
        if not bot_task.done():
            bot_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot_task
        await application.stop()
        await application.shutdown()
        if not server.should_exit:
            server.should_exit = True
        if not server_task.done():
            await server_task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
