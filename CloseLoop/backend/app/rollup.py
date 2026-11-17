"""
Status roll-up.

  SubTask.status -> Task.status -> Plan.status

Pure logic — does NOT commit; caller flushes/commits.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .models import Plan, Task, SubTask, TaskStatus, PlanStatus


def _aggregate(statuses: list[TaskStatus]) -> TaskStatus:
    if not statuses:
        return TaskStatus.NOT_STARTED
    # A future-period child doesn't drag the parent down; if every child is
    # upcoming, so is the parent.
    if all(s == TaskStatus.UPCOMING for s in statuses):
        return TaskStatus.UPCOMING
    statuses = [s for s in statuses if s != TaskStatus.UPCOMING]
    if not statuses:
        return TaskStatus.NOT_STARTED
    if all(s == TaskStatus.COMPLETED for s in statuses):
        return TaskStatus.COMPLETED
    if any(s == TaskStatus.REJECTED for s in statuses):
        return TaskStatus.REJECTED
    if any(s == TaskStatus.PENDING_REVIEW for s in statuses):
        return TaskStatus.PENDING_REVIEW
    if any(s == TaskStatus.ASSIGNED for s in statuses):
        return TaskStatus.ASSIGNED
    if any(s == TaskStatus.IN_PROGRESS for s in statuses):
        return TaskStatus.IN_PROGRESS
    if all(s == TaskStatus.CARRIED_FORWARD for s in statuses):
        return TaskStatus.CARRIED_FORWARD
    return TaskStatus.NOT_STARTED


async def recompute_task(db: AsyncSession, task: Task) -> None:
    rows = await db.execute(select(SubTask.status).where(SubTask.task_id == task.id))
    sub_statuses = [r[0] for r in rows.all()]
    if not sub_statuses:
        return
    task.status = _aggregate(sub_statuses)


async def recompute_plan(db: AsyncSession, plan: Plan) -> None:
    rows = await db.execute(select(Task.status).where(Task.plan_id == plan.id))
    task_statuses = [r[0] for r in rows.all()]
    if not task_statuses:
        return
    agg = _aggregate(task_statuses)
    if agg == TaskStatus.COMPLETED:
        plan.status = PlanStatus.CLOSED
    elif agg in (
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.PENDING_REVIEW,
        TaskStatus.REJECTED,
    ):
        plan.status = PlanStatus.IN_PROGRESS
    else:
        plan.status = PlanStatus.OPEN


async def rollup_from_subtask(db: AsyncSession, subtask: SubTask) -> None:
    """Propagate a subtask's own status (already set by the caller — a
    subtask is always a leaf now, completed directly via
    submit-for-review + review) up to its parent task and plan."""
    task_row = await db.execute(select(Task).where(Task.id == subtask.task_id))
    task = task_row.scalar_one()
    await recompute_task(db, task)
    plan_row = await db.execute(select(Plan).where(Plan.id == task.plan_id))
    plan = plan_row.scalar_one()
    await recompute_plan(db, plan)
