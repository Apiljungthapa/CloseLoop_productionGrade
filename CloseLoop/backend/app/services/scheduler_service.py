"""Background scheduler for automated period closure.

Monitors all tenant periods and automatically closes them when their end_date
is reached (at 23:59:59 UTC). When a period closes:
  1. Closes the current period
  2. Creates the next period
  3. Generates plans from Close Packs
  4. Handles carry-forward
  5. Sends notifications to Team Leads

Scheduler runs as a background task in the FastAPI startup event.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import settings
from ..models import (
    Period,
    PeriodStatus,
    Tenant,
    User,
    Role,
)
from ..calendar import NEPAL_TZ, nepal_today
from .carry_forward_service import close_period
from .audit_service import log_action
from .holiday_service import refresh_holiday_registry_safely
from .notification_service import notify_team_leads_period_closed
from .timesheet_reminder_service import run_reminders

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None
_engine = None
_async_session_factory = None


def get_scheduler_session() -> AsyncSession:
    """Return an async session factory result for use with `async with`.

    This function is intentionally synchronous so callers can use:
        async with get_scheduler_session() as db:
    """
    if not _async_session_factory:
        raise RuntimeError("Scheduler not initialized")
    return _async_session_factory()


async def _close_period_job(tenant_id: str, period_id: str) -> None:
    """Background job that closes a specific period."""
    try:
        async with get_scheduler_session() as db:
            # Fetch period
            result = await db.execute(
                select(Period).where(
                    Period.id == period_id,
                    Period.tenant_id == tenant_id,
                    Period.status.in_([PeriodStatus.OPEN, PeriodStatus.IN_PROGRESS]),
                )
            )
            period = result.scalar_one_or_none()

            if not period:
                logger.warning(
                    f"Period {period_id} for tenant {tenant_id} not found or already closed"
                )
                return

            logger.info(f"Auto-closing period {period_id} (tenant {tenant_id})")

            # Get tenant's SUPER_ADMIN for audit log (system user)
            admin_result = await db.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.role == Role.SUPER_ADMIN,
                )
            )
            system_user = admin_result.scalars().first()
            system_user_id = system_user.id if system_user else None

            # Close the period (this also creates next period, generates plans, etc.)
            closed, next_period, plans_generated, cf_items = await close_period(
                db, tenant_id, period, system_user_id
            )

            # Commit the transaction
            await db.commit()

            logger.info(
                f"Period closed: {period_id} -> {next_period.id}; "
                f"plans generated: {plans_generated}; carry-forward items: {cf_items}"
            )

            # Notify Team Leads asynchronously
            try:
                await notify_team_leads_period_closed(
                    db, tenant_id, closed, next_period, plans_generated
                )
            except Exception as e:
                logger.error(f"Failed to notify team leads: {e}")

    except Exception as e:
        logger.error(f"Error in auto-close job for period {period_id}: {e}", exc_info=True)


def _get_closure_reference_date(now: Optional[datetime] = None) -> datetime.date:
    """Return today's date in Nepal — periods are defined as Nepal-local
    calendar days, so closure must be decided against Nepal's own midnight,
    not the server process's timezone (these containers run UTC, which is
    5h45m behind Nepal — comparing against a UTC date would close a period,
    and generate the next period's plans, up to 5h45m before that period's
    day has actually finished for anyone in Nepal).

    `now` (optional, for tests) may be any aware datetime; it's converted
    into Nepal local time before taking its date.
    """
    if now is not None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(NEPAL_TZ).date()
    return nepal_today()


async def _scheduler_job() -> None:
    """Main scheduler job that checks all tenants/periods for closure."""
    try:
        async with get_scheduler_session() as db:
            # Find all OPEN/IN_PROGRESS periods whose end_date has fully
            # elapsed in Nepal time — strictly BEFORE today, not `<=`. A
            # period is still current for the entirety of its own end_date
            # (e.g. Bhadra 2083 ends Sep 16 — it must stay OPEN for all of
            # Sep 16 Nepal time and only close once Nepal's calendar has
            # actually moved on to Sep 17). Using `<=` here closed periods
            # the instant their last day began, up to a full day early.
            today = _get_closure_reference_date()

            result = await db.execute(
                select(Period).where(
                    Period.status.in_([PeriodStatus.OPEN, PeriodStatus.IN_PROGRESS]),
                    Period.end_date < today,
                )
            )
            periods_to_close = result.scalars().all()

            if periods_to_close:
                logger.info(f"Found {len(periods_to_close)} periods to auto-close")
                for period in periods_to_close:
                    await _close_period_job(period.tenant_id, period.id)
            else:
                logger.debug("No periods to auto-close")

    except Exception as e:
        logger.error(f"Error in scheduler job: {e}", exc_info=True)


async def _timesheet_reminder_job() -> None:
    """Daily timesheet reminders (AD and BS month-end). Never raises."""
    try:
        async with get_scheduler_session() as db:
            await run_reminders(db)
    except Exception as e:
        logger.error(f"Error in timesheet reminder job: {e}", exc_info=True)


async def initialize_scheduler() -> AsyncIOScheduler:
    """Initialize and start the background scheduler."""
    global _scheduler, _engine, _async_session_factory

    # Create async engine and session factory
    _engine = create_async_engine(settings.DATABASE_URL, echo=False)
    _async_session_factory = sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    # Create scheduler
    _scheduler = AsyncIOScheduler()

    # Schedule the job to run every day at 00:01 UTC
    # (checks for periods that ended the previous day)
    _scheduler.add_job(
        _scheduler_job,
        CronTrigger(hour=0, minute=1, timezone="UTC"),
        id="auto_close_periods",
        name="Auto-close expired periods",
        replace_existing=True,
    )

    # Also add a job to check every hour for safety
    _scheduler.add_job(
        _scheduler_job,
        CronTrigger(minute=0, timezone="UTC"),
        id="auto_close_periods_hourly",
        name="Auto-close expired periods (hourly check)",
        replace_existing=True,
    )

    # Re-read the holidays table regularly: writes through /api/holidays reload
    # the registry at once, but a row inserted straight into the database (or a
    # second worker's write) is only seen this way.
    _scheduler.add_job(
        refresh_holiday_registry_safely,
        IntervalTrigger(minutes=10),
        id="refresh_holidays",
        name="Reload configured holidays",
        replace_existing=True,
    )

    # Timesheet reminders: once a day, at the configured Nepal-time hour. Off until
    # the HRMS timesheet can actually be submitted (see config.TIMESHEET_*).
    if settings.TIMESHEET_REMINDERS_ENABLED:
        _scheduler.add_job(
            _timesheet_reminder_job,
            CronTrigger(hour=settings.TIMESHEET_REMINDER_HOUR, minute=0, timezone=NEPAL_TZ),
            id="timesheet_reminders",
            name="Timesheet reminders",
            replace_existing=True,
        )

    # Start the scheduler
    _scheduler.start()
    logger.info("Background scheduler initialized")

    # Run an initial closure check immediately so periods that should already
    # be closed are processed as soon as the backend starts.
    try:
        await _scheduler_job()
    except Exception as e:
        logger.error(f"Initial scheduler check failed: {e}", exc_info=True)

    return _scheduler


async def shutdown_scheduler() -> None:
    """Shut down the scheduler."""
    global _scheduler, _engine

    if _scheduler:
        _scheduler.shutdown()
        logger.info("Scheduler shut down")

    if _engine:
        await _engine.dispose()


def get_scheduler() -> Optional[AsyncIOScheduler]:
    """Get the global scheduler instance."""
    return _scheduler


async def trigger_period_close_manually(
    db: AsyncSession, tenant_id: str, period_id: str, user_id: str
) -> tuple:
    """Admin endpoint to manually trigger period closure."""
    result = await db.execute(
        select(Period).where(
            Period.id == period_id,
            Period.tenant_id == tenant_id,
            Period.status.in_([PeriodStatus.OPEN, PeriodStatus.IN_PROGRESS]),
        )
    )
    period = result.scalar_one_or_none()

    if not period:
        raise ValueError("Period not found or already closed")

    logger.info(
        f"Manual period close triggered by {user_id} for period {period_id} "
        f"(tenant {tenant_id})"
    )

    closed, next_period, plans_generated, cf_items = await close_period(
        db, tenant_id, period, user_id
    )

    await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action="PERIOD_MANUALLY_CLOSED",
        entity_type="Period",
        entity_id=period.id,
        meta={
            "next_period_id": next_period.id,
            "plans_generated": plans_generated,
            "carry_forward_items": cf_items,
        },
    )

    return closed, next_period, plans_generated, cf_items
