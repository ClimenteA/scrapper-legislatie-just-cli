"""Dedicated scheduler process for the monthly index rebuild.

Runs in its own container (1 replica) so the web workers never block and only
one rebuild can be triggered by the schedule.
"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .logger import log
from .reindex import reindex
from .settings import get_settings

BUCHAREST = "Europe/Bucharest"


def scheduled_reindex() -> None:
    try:
        result = reindex(force=False, ocr=get_settings().reindex_ocr)
        log.info(f"Scheduled reindex finished: {result}")
    except Exception:  # noqa: BLE001 - the scheduler must survive a failed run
        log.exception("Scheduled reindex failed; will retry next month.")


def main() -> None:
    if get_settings().reindex_on_start:
        scheduled_reindex()

    scheduler = BlockingScheduler(timezone=BUCHAREST)
    scheduler.add_job(
        scheduled_reindex,
        trigger=CronTrigger(day=15, hour=3, minute=0, timezone=BUCHAREST),
        id="monthly-reindex",
        name="Rebuild legal index on the 15th of each month",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    log.info("Indexer started; next reindex on the 15th at 03:00 Europe/Bucharest.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
