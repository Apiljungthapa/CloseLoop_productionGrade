"""The ONE definition of a "skipped" task, shared by every counter.

Optional work (`is_required = false`, e.g. a Daily recurring task marked
Optional) counts like any other task — in totals, completed counts, progress
and the status buckets — EXCEPT while it is *skipped*: optional, past its due
date and still not done. A skipped task is listed under the Skipped tab but
adds to no total or progress, and is never overdue.

    skipped  =  is_required = false
                AND due_date < today
                AND status IN (NOT_STARTED, ASSIGNED, IN_PROGRESS)

An optional task that is completed, in review, rejected, or not yet due is
counted normally. Counts therefore depend on `today` (Nepal-local, see
`calendar.nepal_today`): an optional task leaves the totals the day after its
due date if nobody did it. A sub-task inherits its parent task's flag.

Mirrors `taskStatusKey` in frontend `leadDashUtils.js` and the Close Bhai
glossary (chatbot_agent `domain_knowledge.py`) — change all three together.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import and_, not_

from ..models import Task, TaskStatus

SKIPPABLE_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.NOT_STARTED,
    TaskStatus.ASSIGNED,
    TaskStatus.IN_PROGRESS,
)


def is_skipped(
    *,
    is_required: Optional[bool],
    status: TaskStatus,
    due_date: Optional[date],
    today: date,
) -> bool:
    """True for an optional work item past its due date and still not done."""
    return (
        is_required is False
        and due_date is not None
        and due_date < today
        and status in SKIPPABLE_STATUSES
    )


def task_is_skipped(task: Task, today: date) -> bool:
    return is_skipped(
        is_required=task.is_required,
        status=task.status,
        due_date=task.due_date,
        today=today,
    )


def skipped_clause(today: date):
    """SQL: the Task rows that are skipped as of `today`."""
    return and_(
        Task.is_required.is_(False),
        Task.due_date.is_not(None),
        Task.due_date < today,
        Task.status.in_(SKIPPABLE_STATUSES),
    )


def counted_clause(today: date):
    """SQL: the Task rows that add to totals / progress as of `today`
    (everything except skipped work)."""
    return not_(skipped_clause(today))
