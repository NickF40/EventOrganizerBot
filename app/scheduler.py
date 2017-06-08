import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.database import session_scope
from app.services.posts import broadcast_post, get_pending_posts


async def process_scheduled_posts(bot) -> None:
    with session_scope() as session:
        posts = get_pending_posts(session)
        for post in posts:
            await broadcast_post(session, bot, post)


def start_scheduler(settings: Settings, bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    async def job():
        await process_scheduled_posts(bot)

    scheduler.add_job(
        lambda: asyncio.create_task(job()), "interval", seconds=settings.scheduler_interval_seconds
    )
    scheduler.start()
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    scheduler.shutdown(wait=False)
