"""
Scheduler — cron-like triggers for agent jobs.
Runs independently, pushes tasks to the queue on schedule.
"""
import asyncio
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.services.task_queue import queue
from app.config import settings
from sqlalchemy import text
from app.database import async_session

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
log = structlog.get_logger()

scheduler = AsyncIOScheduler()


async def trigger_agent_task(agent_id: str, task_type: str, payload: dict = None):
    """Push a scheduled task to the queue."""
    log.info("scheduler.trigger", agent=agent_id, task=task_type)
    await queue.enqueue(agent_id, task_type, payload or {}, priority=5)


def setup_default_schedules():
    """Set up default scheduled tasks."""

    # Email: check inbox every 60 seconds
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(second=f"*/{settings.email_check_interval}"),
        args=["email-agent", "check_inbox"],
        id="email_check",
        replace_existing=True,
    )

    # Finance: daily transaction sync at 6 AM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(hour=6, minute=0),
        args=["finance-agent", "sync_transactions"],
        id="finance_daily_sync",
        replace_existing=True,
    )

    # Finance: anomaly check at 7 AM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(hour=7, minute=0),
        args=["finance-agent", "anomaly_check"],
        id="finance_anomaly",
        replace_existing=True,
    )

    # Finance: month-end close on 1st of each month
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(day=1, hour=8, minute=0),
        args=["finance-agent", "month_end_close"],
        id="finance_month_end",
        replace_existing=True,
    )

    # Marketing: daily campaign sync at 8 AM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(hour=8, minute=0),
        args=["marketing-agent", "sync_campaigns"],
        id="marketing_daily_sync",
        replace_existing=True,
    )

    # Marketing: weekly report on Monday 9 AM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        args=["marketing-agent", "weekly_report"],
        id="marketing_weekly",
        replace_existing=True,
    )

    # Sales: follow-up check at 10 AM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(hour=10, minute=0),
        args=["sales-agent", "follow_up_check"],
        id="sales_followup",
        replace_existing=True,
    )

    # Sales: weekly forecast on Friday 4 PM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(day_of_week="fri", hour=16, minute=0),
        args=["sales-agent", "sales_forecast"],
        id="sales_forecast",
        replace_existing=True,
    )

    # Support: SLA check every 30 minutes
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(minute="*/30"),
        args=["support-agent", "sla_check"],
        id="support_sla",
        replace_existing=True,
    )

    # Support: daily report at 6 PM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(hour=18, minute=0),
        args=["support-agent", "support_report"],
        id="support_daily",
        replace_existing=True,
    )

    # Dev: check deployments every 5 minutes
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(minute="*/5"),
        args=["dev-agent", "check_deployments"],
        id="dev_deployments",
        replace_existing=True,
    )

    # Dev: check open PRs at 9 AM
    scheduler.add_job(
        trigger_agent_task,
        CronTrigger(hour=9, minute=0),
        args=["dev-agent", "monitor_errors"],
        id="dev_errors",
        replace_existing=True,
    )


async def load_custom_schedules():
    """Load user-defined schedules from the database."""
    try:
        async with async_session() as db:
            result = await db.execute(
                text("SELECT agent_id, name, cron_expression, task_type, task_payload FROM schedules WHERE enabled = TRUE"),
            )
            for row in result.fetchall():
                parts = row[2].split()
                if len(parts) == 5:
                    scheduler.add_job(
                        trigger_agent_task,
                        CronTrigger(
                            minute=parts[0], hour=parts[1], day=parts[2],
                            month=parts[3], day_of_week=parts[4],
                        ),
                        args=[row[0], row[3], row[4] or {}],
                        id=f"custom_{row[1]}",
                        replace_existing=True,
                    )
                    log.info("scheduler.custom_loaded", name=row[1], agent=row[0])
    except Exception as e:
        log.warning("scheduler.custom_load_failed", error=str(e))


async def main():
    log.info("scheduler.starting")
    await asyncio.sleep(5)  # Wait for DB

    setup_default_schedules()
    await load_custom_schedules()

    scheduler.start()
    log.info("scheduler.running", jobs=len(scheduler.get_jobs()))

    # Keep alive
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
