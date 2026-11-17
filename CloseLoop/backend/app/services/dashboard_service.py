"""KPI dashboard computations."""

from __future__ import annotations
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from ..calendar import nepal_today
from ..models import (
    Plan,
    Task,
    SubTask,
    PlanStatus,
    TaskStatus,
    Entity,
    CarryForwardItem,
    CarryForwardGroup,
    TimeEntry,
)
from .optional_tasks import counted_clause


_INCOMPLETE = (
    TaskStatus.NOT_STARTED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.REJECTED,
)


async def tenant_dashboard(db: AsyncSession, tenant_id: str) -> dict:
    today = nepal_today()

    plans_total_q = await db.execute(
        select(func.count(Plan.id)).where(Plan.tenant_id == tenant_id)
    )
    total_plans = plans_total_q.scalar() or 0

    plans_open_q = await db.execute(
        select(func.count(Plan.id)).where(
            Plan.tenant_id == tenant_id,
            Plan.status.in_([PlanStatus.OPEN, PlanStatus.IN_PROGRESS]),
        )
    )
    open_plans = plans_open_q.scalar() or 0
    closed_plans = total_plans - open_plans

    # Totals count every task EXCEPT skipped ones (optional, past due, not done
    # — see optional_tasks): a skipped task is neither outstanding work nor
    # part of the completion percentage.
    tasks_total_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id, counted_clause(today)
        )
    )
    total_tasks = tasks_total_q.scalar() or 0

    tasks_done_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            counted_clause(today),
            Task.status == TaskStatus.COMPLETED,
        )
    )
    completed_tasks = tasks_done_q.scalar() or 0

    overdue_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.due_date.is_not(None),
            Task.due_date < today,
            Task.status.in_(_INCOMPLETE),
            counted_clause(today),
        )
    )
    overdue_tasks = overdue_q.scalar() or 0

    cf_q = await db.execute(
        select(func.count(CarryForwardItem.id)).where(
            CarryForwardItem.tenant_id == tenant_id,
        )
    )
    cf_count = cf_q.scalar() or 0

    completion_pct = (
        round((completed_tasks / total_tasks) * 100, 2) if total_tasks else 0.0
    )

    return {
        "total_plans": total_plans,
        "open_plans": open_plans,
        "closed_plans": closed_plans,
        "completion_percentage": completion_pct,
        "overdue_tasks": overdue_tasks,
        "carry_forward_count": cf_count,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
    }


async def entity_dashboard(db: AsyncSession, tenant_id: str, entity_id: str) -> dict:
    today = nepal_today()
    ent_q = await db.execute(
        select(Entity).where(Entity.id == entity_id, Entity.tenant_id == tenant_id)
    )
    ent = ent_q.scalar_one_or_none()
    if not ent:
        raise ValueError("Entity not found")

    total_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.entity_id == entity_id,
            counted_clause(today),
        )
    )
    total = total_q.scalar() or 0

    done_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.entity_id == entity_id,
            counted_clause(today),
            Task.status == TaskStatus.COMPLETED,
        )
    )
    done = done_q.scalar() or 0

    overdue_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.entity_id == entity_id,
            Task.due_date.is_not(None),
            Task.due_date < today,
            Task.status.in_(_INCOMPLETE),
            counted_clause(today),
        )
    )
    overdue = overdue_q.scalar() or 0

    pending_q = await db.execute(
        select(func.count(SubTask.id)).where(
            SubTask.tenant_id == tenant_id,
            SubTask.entity_id == entity_id,
            SubTask.status == TaskStatus.PENDING_REVIEW,
        )
    )
    pending = pending_q.scalar() or 0

    pct = round((done / total) * 100, 2) if total else 0.0
    return {
        "entity_id": ent.id,
        "entity_name": ent.name,
        "completion_percentage": pct,
        "overdue_tasks": overdue,
        "pending_reviews": pending,
        "total_tasks": total,
        "completed_tasks": done,
    }


async def team_lead_dashboard(db: AsyncSession, tenant_id: str, user_id: str) -> dict:
    today = nepal_today()
    assigned_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.assigned_to_id == user_id,
            counted_clause(today),
        )
    )
    assigned = assigned_q.scalar() or 0

    completed_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.assigned_to_id == user_id,
            counted_clause(today),
            Task.status == TaskStatus.COMPLETED,
        )
    )
    completed = completed_q.scalar() or 0

    overdue_q = await db.execute(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.assigned_to_id == user_id,
            Task.due_date.is_not(None),
            Task.due_date < today,
            Task.status.in_(_INCOMPLETE),
            counted_clause(today),
        )
    )
    overdue = overdue_q.scalar() or 0

    reviews_q = await db.execute(
        select(func.count(SubTask.id)).where(
            SubTask.tenant_id == tenant_id,
            SubTask.reviewer_id == user_id,
            SubTask.status == TaskStatus.PENDING_REVIEW,
        )
    )
    reviews = reviews_q.scalar() or 0

    return {
        "user_id": user_id,
        "assigned_tasks": assigned,
        "pending_reviews": reviews,
        "completed_tasks": completed,
        "overdue_tasks": overdue,
    }


async def time_logged_today_minutes(db: AsyncSession, tenant_id: str, user_id: str) -> int:
    """Minutes `user_id` has logged today across every TimeEntry.

    "Today" is the Nepal-local calendar date (`nepal_today`) — the same
    source of truth every other period/task/report computation in this
    module uses — matched against `TimeEntry.entry_date`, which is stamped
    from that same clock when an entry is created (see
    routers/time_entries.py). Feeds the "Time Logged Today" dashboard
    metric for the Team Lead/Team Member banners: always the *caller's
    own* total, never a team-wide sum, regardless of role.
    """
    today = nepal_today()
    q = await db.execute(
        select(func.coalesce(func.sum(TimeEntry.duration_minutes), 0)).where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.user_id == user_id,
            TimeEntry.entry_date == today,
        )
    )
    return q.scalar() or 0
