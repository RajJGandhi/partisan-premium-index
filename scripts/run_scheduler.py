from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.ppi.pipeline import run_daily_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def primary():
    logging.info("Starting primary PPI daily run")
    logging.info("Result: %s", run_daily_pipeline("primary"))


def backup():
    logging.info("Starting backup PPI daily run")
    logging.info("Result: %s", run_daily_pipeline("backup"))


if __name__ == "__main__":
    settings = get_settings()
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    scheduler.add_job(
        primary,
        CronTrigger(hour=settings.primary_run_hour_utc, minute=0),
        id="ppi-primary",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        backup,
        CronTrigger(hour=settings.backup_run_hour_utc, minute=0),
        id="ppi-backup",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logging.info(
        "PPI scheduler running primary=%02d:00 UTC backup=%02d:00 UTC",
        settings.primary_run_hour_utc,
        settings.backup_run_hour_utc,
    )
    scheduler.start()
