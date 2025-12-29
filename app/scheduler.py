import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.database import session_scope
from app.services.posts import broadcast_post, get_pending_posts

logger = logging.getLogger(__name__)


async def process_scheduled_posts(bot) -> None:
    with session_scope() as session:
        posts = get_pending_posts(session)
        if not posts:
            logger.debug("Scheduler run completed - no pending posts.")
            return

        logger.info("Scheduler picked up %s pending posts", len(posts))
        for post in posts:
            logger.info("Processing scheduled post %s", post.id)
            try:
                await broadcast_post(session, bot, post)
            except Exception:
                logger.exception("Unexpected error while broadcasting post %s", post.id)
            else:
                logger.info("Finished broadcasting post %s", post.id)


def start_scheduler(settings: Settings, bot) -> AsyncIOScheduler:
    loop = asyncio.get_running_loop()
    scheduler = AsyncIOScheduler(event_loop=loop)

    async def job():
        logger.debug("Running scheduled broadcast job")
        await process_scheduled_posts(bot)

    scheduler.add_job(job, "interval", seconds=settings.scheduler_interval_seconds)
    scheduler.start()
    logger.info(
        "Scheduler started with interval %ss and timezone %s",
        settings.scheduler_interval_seconds,
        settings.timezone,
    )
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
