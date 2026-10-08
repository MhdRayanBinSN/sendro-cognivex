"""Standalone APScheduler worker for daily research runs."""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.config import get_settings
from app.db import Category, Run, create_db_and_tables, engine
from app.run_service import run_pipeline

logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def run_active_categories() -> None:
    with Session(engine) as session:
        categories = session.exec(select(Category).where(Category.active == True).order_by(Category.id)).all()
        category_names = [category.name for category in categories]
    if not category_names:
        logger.warning("No active categories; scheduled cycle skipped")
        return
    for name in category_names:
        with Session(engine) as session:
            run = Run(category=name)
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
        logger.info("Starting scheduled research run", extra={"run_id": run_id, "category": name})
        await run_pipeline(run_id)


async def main() -> None:
    settings = get_settings()
    create_db_and_tables()
    with Session(engine) as session:
        if session.exec(select(Category)).first() is None:
            session.add(Category(name=settings.category))
            session.commit()
    scheduler = AsyncIOScheduler(timezone="UTC")
    trigger = CronTrigger.from_crontab(settings.schedule_cron, timezone="UTC")
    scheduler.add_job(run_active_categories, trigger, id="daily-product-research", replace_existing=True,
                      coalesce=True, max_instances=1, misfire_grace_time=3600)
    scheduler.start()
    logger.info("Scheduler started", extra={"schedule": settings.schedule_cron, "timezone": "UTC"})
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
