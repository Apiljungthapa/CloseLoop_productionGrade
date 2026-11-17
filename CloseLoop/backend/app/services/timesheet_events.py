"""What did somebody WORK on, and when? - read straight from the tasks.

A person "worked" on a task on the day they SUBMITTED it:

  * a task or sub task they submitted            (submitted_by_id / submitted_at)
  * their own part of a group (entity-wide) task (their done / done_at)

The hours are the Time Taken they entered when they submitted (0 = they left it blank,
and the timesheet asks them to type it). Nothing else makes a row: not a due date, not
an assignment, and not the time-log table, which is never read here.

A submission that was rejected or reopened has its submitter / time cleared by the app
itself, so it stops being work until it is submitted again.

"Filed" work - one that already has a live timesheet line (sent, being sent, or sent
with an unknown result) - is left out, so each submission is filed once per person.
This module is shared by the timesheet page and the reminders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import NEPAL_TZ
from ..models import (
    SubTask,
    SubTaskEntityAssignee,
    Task,
    TaskEntityAssignee,
    TimesheetLine,
    TimesheetLineSource,
)

# Line states in which a submission is "taken" and must not be offered again.
TAKEN = ("SENT", "PENDING", "UNCERTAIN")


@dataclass(frozen=True)
class WorkEvent:
    tenant_id: str
    user_id: str
    kind: str                  # "task" | "subtask"
    unit_id: str
    parent_id: Optional[str]   # a sub task's task
    day: date                  # the day it was submitted (Nepal time)
    minutes: int               # Time Taken entered at submission; 0 = none


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min, tzinfo=NEPAL_TZ),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=NEPAL_TZ),
    )


def _day(moment: datetime) -> date:
    return moment.astimezone(NEPAL_TZ).date()


async def submitted_work(
    db: AsyncSession, start: date, end: date, *, tenant_id: Optional[str] = None, user_id: Optional[str] = None
) -> list[WorkEvent]:
    """Everything submitted in [start, end], filed or not. Filter to one person with
    `user_id` (and `tenant_id`); leave both off for the reminders' all-people sweep."""
    lo, hi = _bounds(start, end)
    found: dict[tuple[str, str, str], WorkEvent] = {}

    # Tasks / sub tasks SUBMITTED by the person.
    q = select(Task.id, Task.tenant_id, Task.submitted_by_id, Task.submitted_at, Task.completion_time_minutes).where(
        Task.submitted_by_id.isnot(None), Task.submitted_at >= lo, Task.submitted_at < hi
    )
    if tenant_id:
        q = q.where(Task.tenant_id == tenant_id)
    if user_id:
        q = q.where(Task.submitted_by_id == user_id)
    for tid, ten, who, at, mins in await db.execute(q):
        found[(who, "task", tid)] = WorkEvent(ten, who, "task", tid, None, _day(at), int(mins or 0))

    q = select(
        SubTask.id, SubTask.tenant_id, SubTask.task_id, SubTask.submitted_by_id, SubTask.submitted_at,
        SubTask.completion_time_minutes,
    ).where(SubTask.submitted_by_id.isnot(None), SubTask.submitted_at >= lo, SubTask.submitted_at < hi)
    if tenant_id:
        q = q.where(SubTask.tenant_id == tenant_id)
    if user_id:
        q = q.where(SubTask.submitted_by_id == user_id)
    for sid, ten, parent, who, at, mins in await db.execute(q):
        found[(who, "subtask", sid)] = WorkEvent(ten, who, "subtask", sid, parent, _day(at), int(mins or 0))

    # Their OWN part of a group task is theirs even when somebody else's click closed the
    # task, so it takes precedence over the task-level record.
    q = select(
        TaskEntityAssignee.task_id, TaskEntityAssignee.tenant_id, TaskEntityAssignee.user_id,
        TaskEntityAssignee.done_at, TaskEntityAssignee.completion_time_minutes,
    ).where(TaskEntityAssignee.done.is_(True), TaskEntityAssignee.done_at >= lo, TaskEntityAssignee.done_at < hi)
    if tenant_id:
        q = q.where(TaskEntityAssignee.tenant_id == tenant_id)
    if user_id:
        q = q.where(TaskEntityAssignee.user_id == user_id)
    for tid, ten, who, at, mins in await db.execute(q):
        found[(who, "task", tid)] = WorkEvent(ten, who, "task", tid, None, _day(at), int(mins or 0))

    q = (
        select(
            SubTaskEntityAssignee.subtask_id, SubTaskEntityAssignee.tenant_id, SubTaskEntityAssignee.user_id,
            SubTaskEntityAssignee.done_at, SubTaskEntityAssignee.completion_time_minutes, SubTask.task_id,
        )
        .join(SubTask, SubTask.id == SubTaskEntityAssignee.subtask_id)
        .where(SubTaskEntityAssignee.done.is_(True), SubTaskEntityAssignee.done_at >= lo,
               SubTaskEntityAssignee.done_at < hi)
    )
    if tenant_id:
        q = q.where(SubTaskEntityAssignee.tenant_id == tenant_id)
    if user_id:
        q = q.where(SubTaskEntityAssignee.user_id == user_id)
    for sid, ten, who, at, mins, parent in await db.execute(q):
        found[(who, "subtask", sid)] = WorkEvent(ten, who, "subtask", sid, parent, _day(at), int(mins or 0))

    return sorted(found.values(), key=lambda e: (e.day, e.user_id, e.kind, e.unit_id))


async def filed_keys(db: AsyncSession, events: list[WorkEvent]) -> set[tuple[str, str, str]]:
    """(user_id, kind, unit_id) of those events that already have a live timesheet line."""
    if not events:
        return set()
    task_ids = {e.unit_id for e in events if e.kind == "task"}
    sub_ids = {e.unit_id for e in events if e.kind == "subtask"}
    rows = await db.execute(
        select(TimesheetLineSource.user_id, TimesheetLineSource.task_id, TimesheetLineSource.subtask_id)
        .join(TimesheetLine, TimesheetLine.id == TimesheetLineSource.line_id)
        .where(
            TimesheetLine.status.in_(TAKEN),
            or_(
                and_(TimesheetLineSource.task_id.isnot(None), TimesheetLineSource.task_id.in_(task_ids or [""])),
                and_(TimesheetLineSource.subtask_id.isnot(None), TimesheetLineSource.subtask_id.in_(sub_ids or [""])),
            ),
        )
    )
    out: set[tuple[str, str, str]] = set()
    for who, tid, sid in rows:
        if tid:
            out.add((who, "task", tid))
        if sid:
            out.add((who, "subtask", sid))
    return out


async def unfiled_work(
    db: AsyncSession, start: date, end: date, *, tenant_id: Optional[str] = None, user_id: Optional[str] = None
) -> tuple[list[WorkEvent], list[WorkEvent]]:
    """(still to file, already filed) among the submissions in the range."""
    events = await submitted_work(db, start, end, tenant_id=tenant_id, user_id=user_id)
    taken = await filed_keys(db, events)
    todo = [e for e in events if (e.user_id, e.kind, e.unit_id) not in taken]
    done = [e for e in events if (e.user_id, e.kind, e.unit_id) in taken]
    return todo, done
