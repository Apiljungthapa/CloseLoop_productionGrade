"""Bridges completion time captured on Task/SubTask submission onto the
TimeEntry ledger.

Team Members report "Time Taken" when they submit a task/sub-task (or
mark their own part done on a shared one) via `ensure_completion_details`
elsewhere — but that value used to be stored ONLY on the work item
(`completion_time_minutes`) and never reached the `time_entries` table.
`dashboard_service.time_logged_today_minutes` and
`report_service.user_workload_report` both read exclusively from
`TimeEntry`, and nothing in the frontend calls `POST /api/time-entries`
directly, so "Time Logged Today" stayed at 0 no matter how much
completion time users reported. Call `record_completion_time_entry` from
every place `completion_time_minutes` is set on a submission so that
number reflects real work.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import nepal_today
from ..models import TimeEntry


async def record_completion_time_entry(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    entity_id: str,
    period_id: Optional[str],
    minutes: Optional[int],
    task_id: Optional[str] = None,
    subtask_id: Optional[str] = None,
) -> None:
    """Log `minutes` of completion time as a TimeEntry for `user_id`.

    No-op when `minutes` is falsy (nothing was actually reported — e.g. a
    lead/admin submission that left Time Taken blank on a path where it
    isn't mandatory). Exactly one of `task_id`/`subtask_id` should be
    given, matching the work item the completion came from. Not
    committed here — callers add this within their own existing
    transaction and commit as usual.
    """
    if not minutes:
        return
    db.add(
        TimeEntry(
            tenant_id=tenant_id,
            user_id=user_id,
            entity_id=entity_id,
            task_id=task_id,
            subtask_id=subtask_id,
            period_id=period_id,
            entry_date=nepal_today(),
            duration_minutes=minutes,
        )
    )
