"""Timesheet reminders: who still owes hours to the HRMS, and telling them.

"Owes" = tasks / sub tasks a person SUBMITTED in a month (AD or BS) that are not yet
part of a live timesheet line, and only for people who exist in the HRMS (a member of
some HRMS project, by work e-mail) - anyone else has no timesheet to fill. The
time-log table is not read.
Nothing to send -> nothing owed -> no reminder, so completing the timesheet is
what stops them. The deadline and stage rules are in timesheet_reminders (pure).

One reminder (bell + e-mail) per person per day, however many months are
behind; each month's reminder is recorded in timesheet_reminder_log, claimed
with INSERT ... ON CONFLICT DO NOTHING before sending, so a restart or a second
worker can never send the same one twice.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import is_non_working_day, nepal_today
from ..config import settings
from ..models import (
    TenantStatus,
    TimesheetReminderLog,
    User,
)
from . import hrms_service
from . import timesheet_events as ev
from . import timesheet_reminders as tr
from .email_service import send_timesheet_reminder_email
from .notification_service import notify_timesheet_reminder

logger = logging.getLogger(__name__)


async def pending_work_by_user(
    db: AsyncSession,
    ob: tr.Obligation,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[tuple[str, str], tuple[int, int]]:
    """(tenant_id, user_id) -> (tasks, minutes) submitted in the obligation's month and not
    yet filed. Sent-with-an-unknown-result work counts too: the person has to settle it."""
    todo, _ = await ev.unfiled_work(db, ob.start, ob.end, tenant_id=tenant_id, user_id=user_id)
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for e in todo:
        tasks, minutes = out.get((e.tenant_id, e.user_id), (0, 0))
        out[(e.tenant_id, e.user_id)] = (tasks + 1, minutes + e.minutes)
    return out


async def hrms_member_emails() -> Optional[set[str]]:
    """E-mails of people who can have an HRMS timesheet, or None if the HRMS
    can't be asked right now (then nobody is reminded rather than everybody)."""
    try:
        return await asyncio.to_thread(hrms_service.get_member_emails)
    except (hrms_service.HrmsNotConfigured, hrms_service.HrmsError):
        logger.warning("Timesheet reminders: could not read the HRMS member list")
        return None


async def obligations_for_user(
    db: AsyncSession, user: User, today: Optional[date] = None,
    eligible_emails: Optional[set[str]] = None,
) -> list[dict]:
    """What the banner shows: months with hours still to send that are within 3
    days of, at, or past their deadline. Empty while reminders are switched off,
    and for anyone who isn't an HRMS member."""
    if not settings.TIMESHEET_REMINDERS_ENABLED or not user.tenant_id:
        return []
    eligible = eligible_emails if eligible_emails is not None else await hrms_member_emails()
    if not eligible or (user.email or "").strip().lower() not in eligible:
        return []
    today = today or nepal_today()
    rows = []
    for ob in tr.obligations(today, settings.TIMESHEET_REMINDER_LOOKBACK):
        status = tr.banner_status(ob, today)
        if not status:
            continue
        tasks, minutes = (await pending_work_by_user(db, ob, tenant_id=user.tenant_id, user_id=user.id)).get(
            (user.tenant_id, user.id), (0, 0)
        )
        if tasks:
            rows.append({
                "calendar": ob.calendar, "period_key": ob.period_key, "label": ob.label,
                "start": ob.start, "end": ob.end, "deadline": ob.deadline, "status": status,
                "days_left": max(0, (ob.deadline - today).days),
                "days_overdue": tr.days_overdue(ob, today), "pending_tasks": tasks, "pending_minutes": minutes,
            })
    order = {tr.OVERDUE: 0, tr.DUE: 1, "UPCOMING": 2}
    return sorted(rows, key=lambda r: (order[r["status"]], r["deadline"]))


def _combined_message(items: list[tuple[tr.Obligation, str, int, int]], today: date) -> str:
    if len(items) == 1:
        ob, st, tasks, minutes = items[0]
        return tr.reminder_message(ob, st, tasks, minutes, today)
    lines = "; ".join(f"{ob.label} ({ob.calendar}) {tasks} task{'s' if tasks != 1 else ''}" for ob, _, tasks, _ in items)
    prefix = "Timesheets overdue" if any(st == tr.OVERDUE for _, st, _, _ in items) else "Timesheets due"
    return f"{prefix}: {lines} you submitted are not yet sent to the HRMS."


async def run_reminders(
    db: AsyncSession, today: Optional[date] = None, eligible_emails: Optional[set[str]] = None
) -> int:
    """Send today's reminders. Returns how many people were reminded. Commits."""
    today = today or nepal_today()
    if is_non_working_day(today):
        return 0
    due = [
        (ob, st)
        for ob in tr.obligations(today, settings.TIMESHEET_REMINDER_LOOKBACK)
        if (st := tr.stage(ob, today))
    ]
    if not due:
        return 0

    eligible = eligible_emails if eligible_emails is not None else await hrms_member_emails()
    if not eligible:
        return 0

    per_user: dict[tuple[str, str], list[tuple[tr.Obligation, str, int, int]]] = defaultdict(list)
    for ob, st in due:
        for key, (tasks, minutes) in (await pending_work_by_user(db, ob)).items():
            per_user[key].append((ob, st, tasks, minutes))
    if not per_user:
        return 0

    users = {
        u.id: u
        for u in (
            await db.execute(select(User).where(User.id.in_([uid for _, uid in per_user])))
        ).scalars()
    }

    reminded = 0
    for (tenant_id, user_id), items in per_user.items():
        user = users.get(user_id)
        if user is None or (user.tenant and user.tenant.status != TenantStatus.active):
            continue
        if (user.email or "").strip().lower() not in eligible:
            continue
        fresh: list[tuple[tr.Obligation, str, int, int]] = []
        for ob, st, tasks, minutes in items:
            claimed = await db.execute(
                pg_insert(TimesheetReminderLog)
                .values(tenant_id=tenant_id, user_id=user_id, calendar=ob.calendar,
                        period_key=ob.period_key, stage=st, sent_on=today)
                .on_conflict_do_nothing(constraint="uq_timesheet_reminder")
                .returning(TimesheetReminderLog.id)
            )
            if claimed.scalar_one_or_none() is not None:
                fresh.append((ob, st, tasks, minutes))
        if not fresh:
            continue
        message = _combined_message(fresh, today)
        await notify_timesheet_reminder(db, tenant_id=tenant_id, user_id=user_id, message=message)
        await db.commit()
        await asyncio.to_thread(
            send_timesheet_reminder_email, user.email, "CloseLoop: timesheet reminder",
            f"{message}\n\nOpen CloseLoop > HRMS Integration to review and submit your timesheet.",
        )
        reminded += 1
    await db.commit()
    logger.info("Timesheet reminders sent to %d user(s)", reminded)
    return reminded
