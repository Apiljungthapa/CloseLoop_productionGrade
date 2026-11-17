"""
Report Engine — Phase 1 core reports.

Implements the five Phase-1 report types defined in the Task 3 spec.
Each public coroutine returns a `ReportResult` (rows + columns + optional
summary) the router converts to JSON / CSV / PDF.

Every query:
  * filters by `tenant_id` first (multi-tenant isolation is non-negotiable);
  * accepts an optional `entity_scope` set returned by
    `report_access_service.resolve_report_entity_scope()` and intersects
    it with the natural filters of the report — so a Group Manager / Team
    Lead automatically sees a narrower slice without the report logic
    needing to branch on role.

Reports do NOT mutate state. Time-tracking aggregation reads only from
the `time_entries` table; the calendar shift, snapshot generation and
carry-forward flows are untouched.

The five reports:
  2.1 period_close_completion_report
  2.2 overdue_task_report
  2.3 user_workload_report
  2.4 year_end_rollup_report
  2.5 time_entry_report
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import Integer, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import nepal_today, working_days_between
from ..models import (
    ClosePack,
    Entity,
    EntityGroup,
    EntityGroupMembership,
    Period,
    PeriodStatus,
    Plan,
    PlanStatus,
    SubTask,
    Task,
    TaskStatus,
    TimeEntry,
    User,
)
from .optional_tasks import counted_clause, is_skipped, task_is_skipped


# ============================================================
# Result type
# ============================================================
@dataclass
class ReportResult:
    """Output of every report function. Format-agnostic."""

    columns: list[str]
    rows: list[dict[str, Any]]
    summary: dict[str, Any] = field(default_factory=dict)


# ============================================================
# Common helpers
# ============================================================
_INCOMPLETE_STATUSES = (
    TaskStatus.NOT_STARTED,
    TaskStatus.ASSIGNED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.REJECTED,
)


async def _entity_ids_in_group(
    db: AsyncSession, tenant_id: str, group_id: str
) -> list[str]:
    """All entities whose primary or secondary group is `group_id`."""
    rows = await db.execute(
        select(Entity.id)
        .outerjoin(EntityGroupMembership, Entity.id == EntityGroupMembership.entity_id)
        .where(
            Entity.tenant_id == tenant_id,
            or_(
                Entity.group_id == group_id,
                EntityGroupMembership.group_id == group_id,
            ),
        )
        .distinct()
    )
    return [r[0] for r in rows.all()]


async def _entity_name_map(
    db: AsyncSession, tenant_id: str, entity_ids: Iterable[str]
) -> dict[str, str]:
    ids = list({e for e in entity_ids if e})
    if not ids:
        return {}
    rows = await db.execute(
        select(Entity.id, Entity.name).where(
            Entity.tenant_id == tenant_id, Entity.id.in_(ids)
        )
    )
    return {eid: ename for eid, ename in rows.all()}


async def _user_name_map(
    db: AsyncSession, tenant_id: str, user_ids: Iterable[str]
) -> dict[str, str]:
    ids = list({u for u in user_ids if u})
    if not ids:
        return {}
    rows = await db.execute(
        select(User.id, User.name).where(User.tenant_id == tenant_id, User.id.in_(ids))
    )
    return {uid: uname for uid, uname in rows.all()}


async def _period_map(
    db: AsyncSession, tenant_id: str, period_ids: Iterable[str]
) -> dict[str, Period]:
    ids = list({p for p in period_ids if p})
    if not ids:
        return {}
    rows = await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.id.in_(ids))
    )
    return {p.id: p for p in rows.scalars().all()}


def _scope_clause(column, entity_scope: Optional[set[str]]):
    """
    Return a SQL clause that limits `column` to the caller's entity scope.

    `None`  → no filter (Super Admin) — returns `True`-equivalent literal.
    set()   → forbid everything       — `False`-equivalent literal.
    """
    if entity_scope is None:
        return column.is_not(None) | column.is_(None)  # always true
    if not entity_scope:
        return column.is_(None) & column.is_not(None)  # always false
    return column.in_(entity_scope)


def _is_on_time(
    completed_at: Optional[datetime | date], due_date: Optional[date]
) -> Optional[bool]:
    """Return True if completed on/before due_date, False if overdue, None when unknown."""
    if completed_at is None or due_date is None:
        return None
    if isinstance(completed_at, datetime):
        completed_d = completed_at.date()
    else:
        completed_d = completed_at
    return completed_d <= due_date


def _days_overdue(due_date: Optional[date], now: date) -> int:
    """Working days (Saturday/Sunday excluded) since `due_date` passed: due
    Friday, it's 1 on Monday and still 0 over the weekend. Still listed as
    overdue either way — only the age count skips weekends."""
    if due_date is None:
        return 0
    return working_days_between(due_date, now)


# ============================================================
# 2.1 — Period Close Completion Report
# ============================================================
async def period_close_completion_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    period_id: str,
    entity_scope: Optional[set[str]],
    group_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> ReportResult:
    """
    Structural snapshot of the close plans in a single period.

    One row per Plan with: entity info, owner/lead name, total/completed
    task counts, completion percentage, due_date, closed_at, on_time
    flag, and a compact task list `tasks_list` describing each task in
    the plan (title, status, lead, completion date, on-time flag).

    Supports group-side-by-side aggregation by filtering on either
    `group_id` (all entities in a group) or a single `entity_id`. The
    `entity_scope` from RBAC is intersected with whichever filter is
    supplied.
    """
    # Resolve period row
    p_row = await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.id == period_id)
    )
    period = p_row.scalar_one_or_none()
    if period is None:
        return ReportResult(columns=_PERIOD_CLOSE_COLUMNS, rows=[], summary={})

    # Build the effective entity filter
    plan_filter = [Plan.tenant_id == tenant_id, Plan.period_id == period_id]

    effective_scope: Optional[set[str]] = entity_scope
    if group_id:
        group_ents = set(await _entity_ids_in_group(db, tenant_id, group_id))
        effective_scope = (
            group_ents if effective_scope is None else (effective_scope & group_ents)
        )
    if entity_id:
        effective_scope = (
            {entity_id} if effective_scope is None else (effective_scope & {entity_id})
        )

    if effective_scope is not None:
        if not effective_scope:
            return ReportResult(
                columns=_PERIOD_CLOSE_COLUMNS,
                rows=[],
                summary={
                    "period_id": period.id,
                    "period_label": period.label,
                    "plans": 0,
                    "completed_plans": 0,
                    "average_completion_percentage": 0.0,
                    "on_time_plans": 0,
                },
            )
        plan_filter.append(Plan.entity_id.in_(effective_scope))

    plans_q = await db.execute(select(Plan).where(*plan_filter).order_by(Plan.title))
    plans = list(plans_q.scalars().all())

    if not plans:
        return ReportResult(
            columns=_PERIOD_CLOSE_COLUMNS,
            rows=[],
            summary={
                "period_id": period.id,
                "period_label": period.label,
                "plans": 0,
                "completed_plans": 0,
                "average_completion_percentage": 0.0,
                "on_time_plans": 0,
            },
        )

    plan_ids = [p.id for p in plans]
    tasks_q = await db.execute(
        select(Task)
        .where(Task.tenant_id == tenant_id, Task.plan_id.in_(plan_ids))
        .order_by(Task.created_at)
    )
    tasks_by_plan: dict[str, list[Task]] = {pid: [] for pid in plan_ids}
    for t in tasks_q.scalars().all():
        tasks_by_plan.setdefault(t.plan_id, []).append(t)

    # Enrichment
    entity_names = await _entity_name_map(db, tenant_id, {p.entity_id for p in plans})
    owner_ids = {p.owner_id for p in plans if p.owner_id}
    task_lead_ids = {
        t.assigned_to_id
        for tasks in tasks_by_plan.values()
        for t in tasks
        if t.assigned_to_id
    }
    user_names = await _user_name_map(db, tenant_id, owner_ids | task_lead_ids)

    rows: list[dict[str, Any]] = []
    on_time_count = 0
    completed_plan_count = 0
    pct_sum = 0.0

    skip_today = nepal_today()
    for plan in plans:
        tasks = tasks_by_plan.get(plan.id, [])
        # Skipped tasks (optional, past due, not done) stay in the per-task list
        # below but are never added to the plan's total, completed count or
        # completion percentage.
        counted = [t for t in tasks if not task_is_skipped(t, skip_today)]
        total = len(counted)
        completed = sum(1 for t in counted if t.status == TaskStatus.COMPLETED)
        pct = round((completed / total) * 100, 2) if total else 0.0

        # Per-task list (compact)
        tasks_list: list[dict[str, Any]] = []
        for t in tasks:
            t_on_time = _is_on_time(
                (getattr(t, "completed_at", None) or t.updated_at)
                if t.status == TaskStatus.COMPLETED
                else None,
                t.due_date,
            )
            tasks_list.append(
                {
                    "task_id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "assigned_to_id": t.assigned_to_id,
                    "assigned_to_name": user_names.get(t.assigned_to_id or "") or None,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "completed_at": (
                        t.updated_at.isoformat()
                        if (t.status == TaskStatus.COMPLETED and t.updated_at)
                        else None
                    ),
                    "on_time": t_on_time,
                }
            )

        # Plan-level on-time: closed and (period.closed_at IS NULL or before period.end_date)
        plan_completed_at: Optional[datetime] = None
        if plan.status == PlanStatus.CLOSED:
            plan_completed_at = period.closed_at or plan.updated_at
            completed_plan_count += 1
        plan_on_time = _is_on_time(plan_completed_at, plan.due_date or period.end_date)
        if plan_on_time is True:
            on_time_count += 1
        pct_sum += pct

        rows.append(
            {
                "plan_id": plan.id,
                "plan_title": plan.title,
                "period_id": period.id,
                "period_label": period.label,
                "entity_id": plan.entity_id,
                "entity_name": entity_names.get(plan.entity_id) or plan.entity_id,
                "owner_id": plan.owner_id,
                "owner_name": (
                    user_names.get(plan.owner_id) if plan.owner_id else None
                ),
                "status": plan.status.value,
                "due_date": plan.due_date.isoformat() if plan.due_date else None,
                "closed_at": (
                    plan_completed_at.isoformat() if plan_completed_at else None
                ),
                "total_tasks": total,
                "completed_tasks": completed,
                "completion_percentage": pct,
                "on_time": plan_on_time,
                "is_overdue": (
                    plan_on_time is False
                    or (
                        plan.status != PlanStatus.CLOSED
                        and plan.due_date is not None
                        and plan.due_date < nepal_today()
                    )
                ),
                "tasks_list": tasks_list,
            }
        )

    summary = {
        "period_id": period.id,
        "period_label": period.label,
        "plans": len(plans),
        "completed_plans": completed_plan_count,
        "on_time_plans": on_time_count,
        "average_completion_percentage": (
            round(pct_sum / len(plans), 2) if plans else 0.0
        ),
    }
    return ReportResult(columns=_PERIOD_CLOSE_COLUMNS, rows=rows, summary=summary)


_PERIOD_CLOSE_COLUMNS = [
    "plan_id",
    "plan_title",
    "period_label",
    "entity_id",
    "entity_name",
    "owner_name",
    "status",
    "total_tasks",
    "completed_tasks",
    "completion_percentage",
    "due_date",
    "closed_at",
    "on_time",
    "is_overdue",
]


# ============================================================
# 2.2 — Overdue Task Report
# ============================================================
async def overdue_task_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    entity_scope: Optional[set[str]],
    period_id: Optional[str] = None,
    today: Optional[date] = None,
) -> ReportResult:
    """
    All incomplete Tasks and SubTasks whose `due_date` < today.

    Sorted by `days_overdue` DESC. Optionally filtered to a single period.
    `entity_scope` from RBAC is applied automatically.
    """
    today = today or nepal_today()
    plan_period_filter = (
        and_(Plan.tenant_id == tenant_id, Plan.period_id == period_id)
        if period_id
        else (Plan.tenant_id == tenant_id)
    )

    # --- Overdue tasks ---
    t_q = (
        select(Task)
        .join(Plan, Task.plan_id == Plan.id)
        .where(
            Task.tenant_id == tenant_id,
            Task.due_date.is_not(None),
            Task.due_date < today,
            Task.status.in_(_INCOMPLETE_STATUSES),
            counted_clause(today),
            plan_period_filter,
            _scope_clause(Task.entity_id, entity_scope),
        )
    )
    tasks = list((await db.execute(t_q)).scalars().all())

    # --- Overdue subtasks ---
    s_q = (
        select(SubTask)
        .join(Task, SubTask.task_id == Task.id)
        .join(Plan, Task.plan_id == Plan.id)
        .where(
            SubTask.tenant_id == tenant_id,
            SubTask.due_date.is_not(None),
            SubTask.due_date < today,
            SubTask.status.in_(_INCOMPLETE_STATUSES),
            plan_period_filter,
            _scope_clause(SubTask.entity_id, entity_scope),
        )
    )
    subs = list((await db.execute(s_q)).scalars().all())

    # Enrichment maps
    entity_ids = {t.entity_id for t in tasks} | {s.entity_id for s in subs}
    entity_names = await _entity_name_map(db, tenant_id, entity_ids)
    user_ids = {t.assigned_to_id for t in tasks if t.assigned_to_id} | {
        s.assigned_to_id for s in subs if s.assigned_to_id
    }
    user_names = await _user_name_map(db, tenant_id, user_ids)
    plan_ids = {t.plan_id for t in tasks} | {s.task_id for s in subs}
    # Resolve plan_id for subtasks via their parent task
    parent_task_ids = {s.task_id for s in subs}
    parent_plan_rows = (
        await db.execute(
            select(Task.id, Task.plan_id).where(Task.id.in_(parent_task_ids))
        )
        if parent_task_ids
        else None
    )
    parent_plan_map: dict[str, str] = {}
    if parent_plan_rows is not None:
        parent_plan_map = {tid: pid for tid, pid in parent_plan_rows.all()}
        plan_ids |= set(parent_plan_map.values())

    plan_title_rows = (
        await db.execute(
            select(Plan.id, Plan.title, Plan.period_id).where(
                Plan.tenant_id == tenant_id, Plan.id.in_(plan_ids)
            )
        )
        if plan_ids
        else None
    )
    plan_meta: dict[str, tuple[str, str]] = {}
    if plan_title_rows is not None:
        plan_meta = {
            pid: (title, pid_period) for pid, title, pid_period in plan_title_rows.all()
        }

    rows: list[dict[str, Any]] = []
    for t in tasks:
        title, _ = plan_meta.get(t.plan_id, (t.plan_id, ""))
        rows.append(
            {
                "item_id": t.id,
                "item_type": "TASK",
                "plan_id": t.plan_id,
                "plan_title": title,
                "entity_id": t.entity_id,
                "entity_name": entity_names.get(t.entity_id) or t.entity_id,
                "title": t.title,
                "status": t.status.value,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "days_overdue": _days_overdue(t.due_date, today),
                "assigned_to_id": t.assigned_to_id,
                "assigned_to_name": user_names.get(t.assigned_to_id or "") or None,
            }
        )
    for s in subs:
        parent_pid = parent_plan_map.get(s.task_id, "")
        title, _ = plan_meta.get(parent_pid, ("", ""))
        rows.append(
            {
                "item_id": s.id,
                "item_type": "SUBTASK",
                "plan_id": parent_pid,
                "plan_title": title,
                "entity_id": s.entity_id,
                "entity_name": entity_names.get(s.entity_id) or s.entity_id,
                "title": s.title,
                "status": s.status.value,
                "due_date": s.due_date.isoformat() if s.due_date else None,
                "days_overdue": _days_overdue(s.due_date, today),
                "assigned_to_id": s.assigned_to_id,
                "assigned_to_name": user_names.get(s.assigned_to_id or "") or None,
            }
        )

    # Sort by days_overdue DESC, then by entity_name then title for stability.
    rows.sort(key=lambda r: (-r["days_overdue"], r["entity_name"], r["title"]))

    summary = {
        "total_overdue": len(rows),
        "overdue_tasks": sum(1 for r in rows if r["item_type"] == "TASK"),
        "overdue_subtasks": sum(1 for r in rows if r["item_type"] == "SUBTASK"),
        "max_days_overdue": max((r["days_overdue"] for r in rows), default=0),
        "as_of": today.isoformat(),
    }
    return ReportResult(columns=_OVERDUE_COLUMNS, rows=rows, summary=summary)


_OVERDUE_COLUMNS = [
    "item_type",
    "entity_name",
    "plan_title",
    "title",
    "status",
    "due_date",
    "days_overdue",
    "assigned_to_name",
    "item_id",
]


# ============================================================
# 2.3 — User Workload and Progress Report
# ============================================================
async def user_workload_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    target_user_id: str,
    period_id: str,
) -> ReportResult:
    """
    Productivity scorecard for a single user in a single period.

    Returns one row per task / subtask assigned to the user (or where
    they are the subtask reviewer) within the period, plus a summary
    dict carrying the scorecard metrics (totals, on-time count,
    overdue_rate, total_timer_hours).
    """
    # Resolve period
    p = (
        await db.execute(
            select(Period).where(Period.tenant_id == tenant_id, Period.id == period_id)
        )
    ).scalar_one_or_none()
    if p is None:
        return ReportResult(columns=_WORKLOAD_COLUMNS, rows=[], summary={})

    u = (
        await db.execute(
            select(User).where(User.tenant_id == tenant_id, User.id == target_user_id)
        )
    ).scalar_one_or_none()
    user_name = u.name if u else target_user_id

    # Plans within this period — needed to scope task/subtask queries by period.
    plan_ids_rows = await db.execute(
        select(Plan.id).where(Plan.tenant_id == tenant_id, Plan.period_id == period_id)
    )
    plan_ids = [pid for (pid,) in plan_ids_rows.all()]

    rows: list[dict[str, Any]] = []
    total_assigned = 0
    completed = 0
    completed_on_time = 0
    overdue_open = 0
    skip_today = nepal_today()

    if plan_ids:
        # Tasks assigned to this user in this period
        tasks_q = await db.execute(
            select(Task).where(
                Task.tenant_id == tenant_id,
                Task.plan_id.in_(plan_ids),
                Task.assigned_to_id == target_user_id,
            )
        )
        for t in tasks_q.scalars().all():
            # Skipped tasks (optional, past due, not done) keep their row but
            # add to no total (assigned, completed, on time) — same rule as
            # every other count.
            counts = not task_is_skipped(t, skip_today)
            if counts:
                total_assigned += 1
            on_time: Optional[bool] = None
            is_overdue = False
            if t.status == TaskStatus.COMPLETED:
                if counts:
                    completed += 1
                on_time = _is_on_time(
                    getattr(t, "completed_at", None) or t.updated_at, t.due_date
                )
                if on_time is True and counts:
                    completed_on_time += 1
            elif t.due_date and t.due_date < skip_today and counts:
                is_overdue = True
                overdue_open += 1
            rows.append(
                {
                    "item_type": "TASK",
                    "item_id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "completed_at": (
                        t.updated_at.isoformat()
                        if (t.status == TaskStatus.COMPLETED and t.updated_at)
                        else None
                    ),
                    "on_time": on_time,
                    "is_overdue": is_overdue,
                    "entity_id": t.entity_id,
                    "assignee_name": user_name,
                }
            )

        # SubTasks assigned to this user (or reviewer) in this period
        sub_q = await db.execute(
            select(SubTask, Task.is_required)
            .join(Task, SubTask.task_id == Task.id)
            .where(
                SubTask.tenant_id == tenant_id,
                Task.plan_id.in_(plan_ids),
                or_(
                    SubTask.assigned_to_id == target_user_id,
                    SubTask.reviewer_id == target_user_id,
                ),
            )
        )
        for s, parent_required in sub_q.all():
            # A sub-task inherits its parent's optional flag: skipped ones are
            # listed, not counted.
            counts = not is_skipped(
                is_required=parent_required, status=s.status,
                due_date=s.due_date, today=skip_today,
            )
            if counts:
                total_assigned += 1
            on_time = None
            is_overdue = False
            if s.status == TaskStatus.COMPLETED:
                if counts:
                    completed += 1
                on_time = _is_on_time(
                    getattr(s, "completed_at", None) or s.updated_at, s.due_date
                )
                if on_time is True and counts:
                    completed_on_time += 1
            elif s.due_date and s.due_date < nepal_today() and counts:
                is_overdue = True
                overdue_open += 1
            rows.append(
                {
                    "item_type": "SUBTASK",
                    "item_id": s.id,
                    "title": s.title,
                    "status": s.status.value,
                    "due_date": s.due_date.isoformat() if s.due_date else None,
                    "completed_at": (
                        s.updated_at.isoformat()
                        if (s.status == TaskStatus.COMPLETED and s.updated_at)
                        else None
                    ),
                    "on_time": on_time,
                    "is_overdue": is_overdue,
                    "entity_id": s.entity_id,
                    "assignee_name": user_name,
                }
            )

    # Total timer hours logged in this period by this user.
    minutes_q = await db.execute(
        select(func.coalesce(func.sum(TimeEntry.duration_minutes), 0)).where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.user_id == target_user_id,
            TimeEntry.entry_date >= p.start_date,
            TimeEntry.entry_date <= p.end_date,
        )
    )
    total_minutes = int(minutes_q.scalar() or 0)
    total_hours = round(total_minutes / 60.0, 2)

    # Enrich rows with entity name (single lookup)
    ent_names = await _entity_name_map(db, tenant_id, {r["entity_id"] for r in rows})
    for r in rows:
        r["entity_name"] = ent_names.get(r["entity_id"]) or r["entity_id"]

    open_items = total_assigned - completed
    overdue_rate = (overdue_open / open_items) if open_items else 0.0

    summary = {
        "user_id": target_user_id,
        "user_name": user_name,
        "period_id": p.id,
        "period_label": p.label,
        "total_assigned": total_assigned,
        "completed": completed,
        "completed_on_time": completed_on_time,
        "open": open_items,
        "overdue_open": overdue_open,
        "overdue_rate": round(overdue_rate, 4),
        "total_timer_hours": total_hours,
        "total_timer_minutes": total_minutes,
    }
    return ReportResult(columns=_WORKLOAD_COLUMNS, rows=rows, summary=summary)


_WORKLOAD_COLUMNS = [
    "item_type",
    "assignee_name",
    "title",
    "entity_name",
    "status",
    "due_date",
    "completed_at",
    "on_time",
    "is_overdue",
    "item_id",
]


# ============================================================
# 2.4 — Year-End Rollup Report
# ============================================================
async def year_end_rollup_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    entity_id: str,
) -> ReportResult:
    """
    Per-period completion metrics + trend for a single entity across all
    13 fiscal periods (P1..P13).

    Each row is one period: completion %, totals, days_to_close, on_time
    flag. Summary carries the avg_completion_percentage,
    avg_days_to_close, and the trend list (period_label,
    completion_percentage).
    """
    ent = (
        await db.execute(
            select(Entity).where(Entity.tenant_id == tenant_id, Entity.id == entity_id)
        )
    ).scalar_one_or_none()
    entity_name = ent.name if ent else entity_id

    periods_q = await db.execute(
        select(Period)
        .where(Period.tenant_id == tenant_id)
        .order_by(Period.start_date)
    )
    periods = list(periods_q.scalars().all())

    rows: list[dict[str, Any]] = []
    pct_values: list[float] = []
    days_values: list[int] = []
    trend: list[dict[str, Any]] = []
    skip_today = nepal_today()

    for p in periods:
        # Totals for this period+entity
        total_q = await db.execute(
            select(func.count(Task.id))
            .join(Plan, Task.plan_id == Plan.id)
            .where(
                Plan.tenant_id == tenant_id,
                Plan.period_id == p.id,
                Plan.entity_id == entity_id,
                counted_clause(skip_today),
            )
        )
        total = total_q.scalar() or 0
        done_q = await db.execute(
            select(func.count(Task.id))
            .join(Plan, Task.plan_id == Plan.id)
            .where(
                Plan.tenant_id == tenant_id,
                Plan.period_id == p.id,
                Plan.entity_id == entity_id,
                counted_clause(skip_today),
                Task.status == TaskStatus.COMPLETED,
            )
        )
        done = done_q.scalar() or 0
        overdue_q = await db.execute(
            select(func.count(Task.id))
            .join(Plan, Task.plan_id == Plan.id)
            .where(
                Plan.tenant_id == tenant_id,
                Plan.period_id == p.id,
                Plan.entity_id == entity_id,
                Task.due_date.is_not(None),
                Task.due_date < nepal_today(),
                Task.status.in_(_INCOMPLETE_STATUSES),
                counted_clause(skip_today),
            )
        )
        overdue = overdue_q.scalar() or 0
        pct = round((done / total) * 100, 2) if total else 0.0

        days_to_close: Optional[int] = None
        if p.closed_at and p.start_date:
            days_to_close = (p.closed_at.date() - p.start_date).days
            if days_to_close < 0:
                days_to_close = 0
            days_values.append(days_to_close)

        on_time: Optional[bool] = None
        if p.closed_at:
            on_time = p.closed_at.date() <= p.end_date

        if total > 0:
            pct_values.append(pct)

        trend.append(
            {
                "period_id": p.id,
                "period_label": p.label,
                "completion_percentage": pct,
            }
        )

        rows.append(
            {
                "period_id": p.id,
                "period_label": p.label,
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "status": p.status.value,
                "total_tasks": total,
                "completed_tasks": done,
                "overdue_tasks": overdue,
                "completion_percentage": pct,
                "days_to_close": days_to_close,
                "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                "on_time": on_time,
            }
        )

    summary = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "periods_reported": len(periods),
        "average_completion_percentage": (
            round(sum(pct_values) / len(pct_values), 2) if pct_values else 0.0
        ),
        "average_days_to_close": (
            round(sum(days_values) / len(days_values), 2) if days_values else None
        ),
        "closed_periods": sum(1 for r in rows if r["closed_at"]),
        "on_time_closed_periods": sum(1 for r in rows if r["on_time"] is True),
        "trend": trend,
    }
    return ReportResult(columns=_YEAR_END_COLUMNS, rows=rows, summary=summary)


_YEAR_END_COLUMNS = [
    "period_label",
    "status",
    "start_date",
    "end_date",
    "total_tasks",
    "completed_tasks",
    "overdue_tasks",
    "completion_percentage",
    "days_to_close",
    "closed_at",
    "on_time",
]


# ============================================================
# 2.5 — Time Entry Report
# ============================================================
async def time_entry_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    entity_scope: Optional[set[str]],
    user_scope: Optional[set[str]],
    start_date: date,
    end_date: date,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    period_id: Optional[str] = None,
) -> ReportResult:
    """
    Itemized time entries between two dates (inclusive). Used for
    external spreadsheet reconciliation, so the row shape is flat and
    every column is a scalar.

    RBAC: `entity_scope` and `user_scope` are intersected with explicit
    `entity_id` / `user_id` filters.
    """
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    filters = [
        TimeEntry.tenant_id == tenant_id,
        TimeEntry.entry_date >= start_date,
        TimeEntry.entry_date <= end_date,
    ]
    if period_id:
        filters.append(TimeEntry.period_id == period_id)

    # Entity filter — intersect explicit + RBAC scope
    effective_entity_scope: Optional[set[str]] = entity_scope
    if entity_id:
        effective_entity_scope = (
            {entity_id}
            if effective_entity_scope is None
            else effective_entity_scope & {entity_id}
        )
    if effective_entity_scope is not None:
        if not effective_entity_scope:
            return ReportResult(
                columns=_TIME_ENTRY_COLUMNS,
                rows=[],
                summary={
                    "total_entries": 0,
                    "total_hours": 0.0,
                    "total_minutes": 0,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
        filters.append(TimeEntry.entity_id.in_(effective_entity_scope))

    # User filter — intersect explicit + RBAC scope
    effective_user_scope: Optional[set[str]] = user_scope
    if user_id:
        effective_user_scope = (
            {user_id}
            if effective_user_scope is None
            else effective_user_scope & {user_id}
        )
    if effective_user_scope is not None:
        if not effective_user_scope:
            return ReportResult(
                columns=_TIME_ENTRY_COLUMNS,
                rows=[],
                summary={
                    "total_entries": 0,
                    "total_hours": 0.0,
                    "total_minutes": 0,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
        filters.append(TimeEntry.user_id.in_(effective_user_scope))

    entries_q = await db.execute(
        select(TimeEntry)
        .where(*filters)
        .order_by(TimeEntry.entry_date, TimeEntry.user_id)
    )
    entries = list(entries_q.scalars().all())

    # Enrichment maps
    user_names = await _user_name_map(db, tenant_id, {e.user_id for e in entries})
    entity_names = await _entity_name_map(db, tenant_id, {e.entity_id for e in entries})

    # Work-item titles in one shot per kind
    task_ids = {e.task_id for e in entries if e.task_id}
    subtask_ids = {e.subtask_id for e in entries if e.subtask_id}

    task_titles: dict[str, str] = {}
    if task_ids:
        rows_ = await db.execute(
            select(Task.id, Task.title).where(
                Task.tenant_id == tenant_id, Task.id.in_(task_ids)
            )
        )
        task_titles = {tid: title for tid, title in rows_.all()}
    sub_titles: dict[str, str] = {}
    if subtask_ids:
        rows_ = await db.execute(
            select(SubTask.id, SubTask.title).where(
                SubTask.tenant_id == tenant_id, SubTask.id.in_(subtask_ids)
            )
        )
        sub_titles = {sid: title for sid, title in rows_.all()}

    rows: list[dict[str, Any]] = []
    total_minutes = 0
    for e in entries:
        if e.subtask_id:
            kind = "SUBTASK"
            title = sub_titles.get(e.subtask_id) or e.subtask_id
            item_id = e.subtask_id
        elif e.task_id:
            kind = "TASK"
            title = task_titles.get(e.task_id) or e.task_id
            item_id = e.task_id
        else:
            kind = "FREEFORM"
            title = "(unbound)"
            item_id = ""
        total_minutes += e.duration_minutes
        rows.append(
            {
                "entry_id": e.id,
                "entry_date": e.entry_date.isoformat(),
                "user_id": e.user_id,
                "user_name": user_names.get(e.user_id) or e.user_id,
                "entity_id": e.entity_id,
                "entity_name": entity_names.get(e.entity_id) or e.entity_id,
                "task_type": kind,
                "item_id": item_id,
                "item_title": title,
                "duration_minutes": e.duration_minutes,
                "hours": round(e.duration_minutes / 60.0, 2),
                "started_at": e.started_at.isoformat() if e.started_at else None,
                "ended_at": e.ended_at.isoformat() if e.ended_at else None,
                "note": e.note or "",
            }
        )

    summary = {
        "total_entries": len(rows),
        "total_minutes": total_minutes,
        "total_hours": round(total_minutes / 60.0, 2),
        "distinct_users": len({r["user_id"] for r in rows}),
        "distinct_entities": len({r["entity_id"] for r in rows}),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    return ReportResult(columns=_TIME_ENTRY_COLUMNS, rows=rows, summary=summary)


_TIME_ENTRY_COLUMNS = [
    "entry_date",
    "user_name",
    "entity_name",
    "task_type",
    "item_title",
    "hours",
    "duration_minutes",
    "started_at",
    "ended_at",
    "note",
    "entry_id",
]


# ============================================================
# Current Period Status Report
# ============================================================
_CURRENT_PERIOD_COLUMNS = [
    "item_type",
    "pack_name",
    "entity_name",
    "title",
    "status",
    "due_date",
    "late_days",
    "assignee_name",
    "carried_from",
    "item_id",
]


def _late_days(obj) -> int:
    """Calendar days between an item's due date and when it actually
    reached COMPLETED (0 = on time / still open / no due date)."""
    completed_at = getattr(obj, "completed_at", None)
    due = getattr(obj, "due_date", None)
    if getattr(obj, "status", None) != TaskStatus.COMPLETED or not completed_at or not due:
        return 0
    return max(0, (completed_at.date() - due).days)

# Statuses that still need work (task or subtask) — anything not here and
# not COMPLETED counts as "pending".
_CP_IN_PROGRESS = (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED)


async def _resolve_report_period(
    db: AsyncSession, tenant_id: str, period_id: Optional[str]
) -> Optional[Period]:
    """The period a Current-Period report should describe: an explicit
    `period_id`, else the tenant period whose date range covers today,
    else the earliest still-open period."""
    if period_id:
        row = await db.execute(
            select(Period).where(
                Period.tenant_id == tenant_id, Period.id == period_id
            )
        )
        return row.scalar_one_or_none()

    today = nepal_today()
    row = await db.execute(
        select(Period)
        .where(
            Period.tenant_id == tenant_id,
            Period.start_date <= today,
            Period.end_date >= today,
        )
        .order_by(Period.start_date.desc())
        .limit(1)
    )
    p = row.scalar_one_or_none()
    if p:
        return p
    row = await db.execute(
        select(Period)
        .where(
            Period.tenant_id == tenant_id,
            Period.status.in_([PeriodStatus.OPEN, PeriodStatus.IN_PROGRESS]),
        )
        .order_by(Period.start_date)
        .limit(1)
    )
    return row.scalar_one_or_none()


async def current_period_status_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    entity_scope: Optional[set[str]],
    period_id: Optional[str] = None,
    group_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> ReportResult:
    """
    The full task-by-task status of every Close-Pack task/subtask that
    lands in the current period (or an explicitly requested one).

    This is the report that used to render inline on the Close Packs page
    — moved into the Report Engine so it is exportable (CSV/PDF), RBAC-
    scoped, and available from the navbar Reports section. Row = one Task
    or one SubTask. Summary carries the headline counters
    (total / completed / pending / in_progress / carried_forward / missed
    / unassigned) plus completion_pct and the period's AD + BS labels.
    """
    today = nepal_today()
    period = await _resolve_report_period(db, tenant_id, period_id)

    empty_summary = {
        "period_id": None,
        "period_label": None,
        "bs_start_label": None,
        "bs_end_label": None,
        "total": 0,
        "completed": 0,
        "pending": 0,
        "in_progress": 0,
        "pending_review": 0,
        "rejected": 0,
        "carried_forward": 0,
        "missed": 0,
        "completed_late": 0,
        "assigned": 0,
        "unassigned": 0,
        "subtasks_total": 0,
        "subtasks_completed": 0,
        "completion_pct": 0.0,
    }
    if period is None:
        return ReportResult(columns=_CURRENT_PERIOD_COLUMNS, rows=[], summary=empty_summary)

    # BS labels for the period (best-effort — never break the report).
    bs_start_label = bs_end_label = None
    try:
        from .period_service import bs_labels_for

        bs = bs_labels_for(period.start_date, period.end_date)
        bs_start_label = bs.get("bs_start_label")
        bs_end_label = bs.get("bs_end_label")
    except Exception:  # noqa: BLE001
        pass

    # Effective entity filter: RBAC scope ∩ group ∩ entity_id.
    effective_scope: Optional[set[str]] = entity_scope
    if group_id:
        group_ents = set(await _entity_ids_in_group(db, tenant_id, group_id))
        effective_scope = (
            group_ents if effective_scope is None else (effective_scope & group_ents)
        )
    if entity_id:
        effective_scope = (
            {entity_id} if effective_scope is None else (effective_scope & {entity_id})
        )

    summary = dict(empty_summary)
    summary.update(
        period_id=period.id,
        period_label=period.label,
        bs_start_label=bs_start_label,
        bs_end_label=bs_end_label,
    )

    plan_filter = [
        Plan.tenant_id == tenant_id,
        Plan.period_id == period.id,
        Plan.source_pack_id.is_not(None),  # Close-Pack plans only
    ]
    if effective_scope is not None:
        if not effective_scope:
            return ReportResult(columns=_CURRENT_PERIOD_COLUMNS, rows=[], summary=summary)
        plan_filter.append(Plan.entity_id.in_(effective_scope))

    plans = list((await db.execute(select(Plan).where(*plan_filter))).scalars().all())
    if not plans:
        return ReportResult(columns=_CURRENT_PERIOD_COLUMNS, rows=[], summary=summary)

    plan_ids = [p.id for p in plans]
    pack_by_plan = {p.id: p.source_pack_id for p in plans}

    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.tenant_id == tenant_id, Task.plan_id.in_(plan_ids))
                .order_by(Task.created_at, Task.title)
            )
        ).scalars().all()
    )
    task_ids = [t.id for t in tasks]
    subs_by_task: dict[str, list[SubTask]] = {}
    if task_ids:
        for s in (
            await db.execute(
                select(SubTask)
                .where(SubTask.tenant_id == tenant_id, SubTask.task_id.in_(task_ids))
                .order_by(SubTask.created_at, SubTask.title)
            )
        ).scalars().all():
            subs_by_task.setdefault(s.task_id, []).append(s)

    pack_rows = await db.execute(
        select(ClosePack.id, ClosePack.name).where(
            ClosePack.tenant_id == tenant_id,
            ClosePack.id.in_({pid for pid in pack_by_plan.values() if pid}),
        )
    )
    pack_names = {pid: name for pid, name in pack_rows.all()}
    entity_names = await _entity_name_map(db, tenant_id, {t.entity_id for t in tasks})
    assignee_ids = {t.assigned_to_id or t.member_assigned_to_id for t in tasks} - {None}
    for slist in subs_by_task.values():
        assignee_ids |= {s.assigned_to_id for s in slist if s.assigned_to_id}
    user_names = await _user_name_map(db, tenant_id, assignee_ids)

    def _assigned(o) -> bool:
        return bool(
            getattr(o, "assigned_to_id", None)
            or getattr(o, "member_assigned_to_id", None)
            or getattr(o, "assigned_entity_wide", False)
        )

    def _is_placeholder(o) -> bool:
        # a "scheduled" placeholder: locked, and due outside this period
        return (
            o.status == TaskStatus.UPCOMING
            and (o.due_date is None
                 or o.due_date < period.start_date
                 or o.due_date > period.end_date)
        )

    rows: list[dict[str, Any]] = []
    for t in tasks:
        if _is_placeholder(t):
            continue  # not work due this period — a "coming up" marker only
        pack_name = pack_names.get(pack_by_plan.get(t.plan_id) or "") or "—"
        ent_name = entity_names.get(t.entity_id) or t.entity_id
        # A skipped task (optional, past due, not done) gets its row but is never
        # added to the headline counters — its increments land on a throw-away copy.
        counters = dict(summary) if task_is_skipped(t, today) else summary
        counters["total"] += 1
        if t.status == TaskStatus.COMPLETED:
            counters["completed"] += 1
        elif t.status == TaskStatus.PENDING_REVIEW:
            counters["pending_review"] += 1
        elif t.status == TaskStatus.REJECTED:
            counters["rejected"] += 1
        elif t.status in _CP_IN_PROGRESS:
            counters["in_progress"] += 1
        elif t.status == TaskStatus.CARRIED_FORWARD:
            counters["carried_forward"] += 1
        else:
            counters["pending"] += 1
        if _assigned(t):
            counters["assigned"] += 1
        else:
            counters["unassigned"] += 1
        if t.status != TaskStatus.COMPLETED and t.due_date and t.due_date < today:
            counters["missed"] += 1
        t_late = _late_days(t)
        if t_late:
            counters["completed_late"] += 1

        rows.append(
            {
                "item_type": "TASK",
                "pack_name": pack_name,
                "entity_name": ent_name,
                "title": t.title,
                "status": (
                    f"COMPLETED ({t_late}d late)" if t_late else t.status.value
                ),
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "late_days": t_late or None,
                "assignee_name": user_names.get(
                    t.assigned_to_id or t.member_assigned_to_id or ""
                )
                or (None if _assigned(t) else "Unassigned"),
                "carried_from": t.carried_from_label,
                "item_id": t.id,
            }
        )

        for s in subs_by_task.get(t.id, []):
            counters["subtasks_total"] += 1
            if s.status == TaskStatus.COMPLETED:
                counters["subtasks_completed"] += 1
            s_late = _late_days(s)
            if s_late:
                counters["completed_late"] += 1
            rows.append(
                {
                    "item_type": "SUBTASK",
                    "pack_name": pack_name,
                    "entity_name": ent_name,
                    "title": f"↳ {s.title}",
                    "status": (
                        f"COMPLETED ({s_late}d late)" if s_late else s.status.value
                    ),
                    "due_date": s.due_date.isoformat() if s.due_date else None,
                    "late_days": s_late or None,
                    "assignee_name": user_names.get(s.assigned_to_id or "")
                    or (None if _assigned(s) else "Unassigned"),
                    "carried_from": s.carried_from_label,
                    "item_id": s.id,
                }
            )

    summary["completion_pct"] = (
        round(summary["completed"] / summary["total"], 4) if summary["total"] else 0.0
    )
    return ReportResult(columns=_CURRENT_PERIOD_COLUMNS, rows=rows, summary=summary)
