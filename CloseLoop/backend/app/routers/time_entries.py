"""
Time Entry router.

Supports the Report Engine (Task 3 §2.3 and §2.5) by giving users a way
to log blocks of time against any leaf work item — a Task or a SubTask.
Each entry is later aggregated by `time_entry_report` (by user/entity/date
range) and `user_workload_report` (sum of minutes in a period).

Endpoints:

    POST   /api/time-entries
    GET    /api/time-entries
    GET    /api/time-entries/my/summary
    GET    /api/time-entries/user/{user_id}/summary   (Team Lead only)
    GET    /api/time-entries/{entry_id}
    PATCH  /api/time-entries/{entry_id}
    DELETE /api/time-entries/{entry_id}

Rules:

  * Exactly ONE of (task_id, subtask_id) must be set on create. The
    router derives `entity_id` from the chosen work item, and
    `period_id` from the work item's parent Plan.
  * Time entries are owned by the logging user. A user can only edit /
    delete their own entries — except SUPER_ADMIN, who may operate on
    any entry inside their tenant.
  * Listing is RBAC-scoped via `report_access_service` —
    Team Members see only their own entries; Team Leads see entries for
    users + entities in their scope; Super Admins see everything in the
    tenant.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_roles, require_tenant
from ..calendar import nepal_today
from ..database import get_db
from ..models import (
    Entity,
    Plan,
    Role,
    SubTask,
    Task,
    TimeEntry,
    User,
)
from ..schemas import (
    MyTimeSummaryOut,
    TimeDayOut,
    TimeEntryCreate,
    TimeEntryOut,
    TimeEntryUpdate,
    TimeMonthOut,
    TimeWeekOut,
    UserMini,
)
from ..services import report_access_service as access
from ..services.audit_service import log_action
from ..websocket import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/time-entries", tags=["time-entries"])


async def _notify_owner(user_id: str, event: str, entry: TimeEntry) -> None:
    """Push a lightweight event to the entry owner's own connections so
    their "Time Logged Today" dashboard metric refreshes live — never
    blocks the request if the socket layer misbehaves (same guard as
    routers/close_packs.py's plan:updated broadcast)."""
    try:
        await manager.send_user(
            user_id,
            event,
            {"id": entry.id, "entry_date": entry.entry_date},
        )
    except Exception:  # noqa: BLE001
        logger.exception("%s notify failed for time entry %s", event, entry.id)


# ============================================================
# Helpers
# ============================================================
def _is_admin(user: User) -> bool:
    return user.role in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER)


async def _resolve_work_item_context(
    db: AsyncSession,
    tenant_id: str,
    *,
    task_id: Optional[str],
    subtask_id: Optional[str],
) -> tuple[str, Optional[str]]:
    """
    Return ``(entity_id, period_id)`` for the chosen work item.

    Exactly ONE of the two ids must be supplied; ``period_id`` is the
    parent Plan's period (None if the chain cannot resolve, which keeps
    the entry still useful for raw time-range reports).
    """
    supplied = [
        ("task_id", task_id),
        ("subtask_id", subtask_id),
    ]
    filled = [name for name, value in supplied if value]
    if len(filled) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Exactly one of task_id, subtask_id must be set. "
                f"Received: {filled or 'none'}"
            ),
        )

    if task_id:
        row = await db.execute(
            select(Task.entity_id, Task.plan_id).where(
                Task.tenant_id == tenant_id, Task.id == task_id
            )
        )
        result = row.one_or_none()
        if not result:
            raise HTTPException(404, "Task not found")
        entity_id, plan_id = result
        period_id = await _period_for_plan(db, tenant_id, plan_id)
        return entity_id, period_id

    # subtask_id
    row = await db.execute(
        select(SubTask.entity_id, Task.plan_id)
        .join(Task, SubTask.task_id == Task.id)
        .where(SubTask.tenant_id == tenant_id, SubTask.id == subtask_id)
    )
    result = row.one_or_none()
    if not result:
        raise HTTPException(404, "SubTask not found")
    entity_id, plan_id = result
    period_id = await _period_for_plan(db, tenant_id, plan_id)
    return entity_id, period_id


async def _period_for_plan(
    db: AsyncSession, tenant_id: str, plan_id: str
) -> Optional[str]:
    row = await db.execute(
        select(Plan.period_id).where(Plan.tenant_id == tenant_id, Plan.id == plan_id)
    )
    return row.scalar_one_or_none()


def _serialize(
    entry: TimeEntry,
    *,
    user: Optional[User],
    entity_name: Optional[str],
    work_item_title: Optional[str],
    work_item_type: Optional[str],
) -> TimeEntryOut:
    return TimeEntryOut(
        id=entry.id,
        tenant_id=entry.tenant_id,
        user_id=entry.user_id,
        entity_id=entry.entity_id,
        task_id=entry.task_id,
        subtask_id=entry.subtask_id,
        period_id=entry.period_id,
        entry_date=entry.entry_date,
        started_at=entry.started_at,
        ended_at=entry.ended_at,
        duration_minutes=entry.duration_minutes,
        note=entry.note,
        created_at=entry.created_at,
        user=(
            UserMini(id=user.id, name=user.name, title=user.title)
            if user
            else None
        ),
        entity_name=entity_name,
        work_item_title=work_item_title,
        work_item_type=work_item_type,
    )


async def _enrich_one(db: AsyncSession, entry: TimeEntry) -> TimeEntryOut:
    tenant_id = entry.tenant_id
    user = (
        await db.execute(
            select(User).where(User.id == entry.user_id, User.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    entity_name = (
        await db.execute(
            select(Entity.name).where(
                Entity.id == entry.entity_id, Entity.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()

    work_item_title: Optional[str] = None
    work_item_type: Optional[str] = None
    if entry.subtask_id:
        work_item_type = "SUBTASK"
        work_item_title = (
            await db.execute(
                select(SubTask.title).where(
                    SubTask.id == entry.subtask_id, SubTask.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
    elif entry.task_id:
        work_item_type = "TASK"
        work_item_title = (
            await db.execute(
                select(Task.title).where(
                    Task.id == entry.task_id, Task.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()

    return _serialize(
        entry,
        user=user,
        entity_name=entity_name,
        work_item_title=work_item_title,
        work_item_type=work_item_type,
    )


async def _enrich_many(
    db: AsyncSession, entries: list[TimeEntry]
) -> list[TimeEntryOut]:
    if not entries:
        return []
    # All entries here are already tenant-filtered by the caller (they come
    # from a single-tenant query) — reuse that tenant_id to scope every
    # batch lookup below rather than trusting the collected ids alone.
    tenant_id = entries[0].tenant_id
    user_ids = {e.user_id for e in entries}
    entity_ids = {e.entity_id for e in entries}
    task_ids = {e.task_id for e in entries if e.task_id}
    subtask_ids = {e.subtask_id for e in entries if e.subtask_id}

    users = (
        (
            await db.execute(
                select(User).where(
                    User.id.in_(user_ids), User.tenant_id == tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    user_map = {u.id: u for u in users}
    entity_rows = (
        await db.execute(
            select(Entity.id, Entity.name).where(
                Entity.id.in_(entity_ids), Entity.tenant_id == tenant_id
            )
        )
    ).all()
    entity_name_map = {eid: ename for eid, ename in entity_rows}

    task_title_map: dict[str, str] = {}
    if task_ids:
        rows = await db.execute(
            select(Task.id, Task.title).where(
                Task.id.in_(task_ids), Task.tenant_id == tenant_id
            )
        )
        task_title_map = {tid: title for tid, title in rows.all()}
    subtask_title_map: dict[str, str] = {}
    if subtask_ids:
        rows = await db.execute(
            select(SubTask.id, SubTask.title).where(
                SubTask.id.in_(subtask_ids), SubTask.tenant_id == tenant_id
            )
        )
        subtask_title_map = {sid: title for sid, title in rows.all()}

    out: list[TimeEntryOut] = []
    for e in entries:
        if e.subtask_id:
            wi_type, wi_title = "SUBTASK", subtask_title_map.get(e.subtask_id)
        elif e.task_id:
            wi_type, wi_title = "TASK", task_title_map.get(e.task_id)
        else:
            wi_type, wi_title = None, None
        out.append(
            _serialize(
                e,
                user=user_map.get(e.user_id),
                entity_name=entity_name_map.get(e.entity_id),
                work_item_title=wi_title,
                work_item_type=wi_type,
            )
        )
    return out


# ============================================================
# CREATE
# ============================================================
@router.post("", response_model=TimeEntryOut, status_code=status.HTTP_201_CREATED)
async def create_time_entry(
    payload: TimeEntryCreate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Log a block of time. The entry is recorded against the calling user
    (admins still log against themselves — there is no `as_user_id`
    override). The router derives ``entity_id`` and ``period_id`` from
    the chosen work item.
    """
    if not me.tenant_id:
        raise HTTPException(403, "Tenant context required")

    entity_id, period_id = await _resolve_work_item_context(
        db,
        me.tenant_id,
        task_id=payload.task_id,
        subtask_id=payload.subtask_id,
    )

    entry = TimeEntry(
        tenant_id=me.tenant_id,
        user_id=me.id,
        entity_id=entity_id,
        task_id=payload.task_id,
        subtask_id=payload.subtask_id,
        period_id=period_id,
        entry_date=payload.entry_date or nepal_today(),
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        duration_minutes=payload.duration_minutes,
        note=payload.note,
    )
    db.add(entry)
    await db.flush()
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TIME_ENTRY_CREATED",
        entity_type="TimeEntry",
        entity_id=entry.id,
        meta={
            "duration_minutes": entry.duration_minutes,
            "entity_id": entry.entity_id,
            "work_item": "subtask" if entry.subtask_id else "task",
        },
    )
    await db.commit()
    await db.refresh(entry)
    await _notify_owner(entry.user_id, "time_entry:created", entry)
    return await _enrich_one(db, entry)


# ============================================================
# LIST
# ============================================================
@router.get("", response_model=list[TimeEntryOut])
async def list_time_entries(
    user_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    subtask_id: Optional[str] = Query(None),
    period_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """List time entries scoped to what the caller is allowed to see."""
    if not me.tenant_id:
        raise HTTPException(403, "Tenant context required")

    entity_scope = await access.resolve_report_entity_scope(db, me)
    user_scope = await access.resolve_report_user_scope(db, me)

    # Empty-scope short-circuit (caller has zero accessible entities/users).
    if entity_scope is not None and not entity_scope:
        return []
    if user_scope is not None and not user_scope:
        return []

    filters = [TimeEntry.tenant_id == me.tenant_id]
    if entity_scope is not None:
        filters.append(TimeEntry.entity_id.in_(entity_scope))
    if user_scope is not None:
        filters.append(TimeEntry.user_id.in_(user_scope))

    if user_id:
        if user_id != me.id:
            await access.ensure_can_view_user(db, me, user_id)
        filters.append(TimeEntry.user_id == user_id)
    if entity_id:
        await access.ensure_can_view_entity(db, me, entity_id)
        filters.append(TimeEntry.entity_id == entity_id)
    if task_id:
        filters.append(TimeEntry.task_id == task_id)
    if subtask_id:
        filters.append(TimeEntry.subtask_id == subtask_id)
    if period_id:
        filters.append(TimeEntry.period_id == period_id)
    if start_date:
        filters.append(TimeEntry.entry_date >= start_date)
    if end_date:
        filters.append(TimeEntry.entry_date <= end_date)

    rows = await db.execute(
        select(TimeEntry)
        .where(*filters)
        .order_by(TimeEntry.entry_date.desc(), TimeEntry.created_at.desc())
        .limit(limit)
    )
    entries = list(rows.scalars().all())
    return await _enrich_many(db, entries)


# ============================================================
# MY SUMMARY (time-log history page)
# ============================================================
SUMMARY_WEEKS = 8
SUMMARY_MONTHS = 6


def _week_start(d: date) -> date:
    """Sunday on/before `d` (Nepal's week starts on Sunday)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _shift_month(d: date, months: int) -> date:
    """First day of the month `months` away from `d`'s month."""
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


async def _build_time_summary(
    db: AsyncSession, tenant_id: str, user_id: str, days: int
) -> MyTimeSummaryOut:
    """Daily series for the last `days` days plus today / this-week /
    this-month totals and the last few weeks and months, for ONE user.
    Callers decide who may see `user_id`."""
    today = nepal_today()
    chart_start = today - timedelta(days=days - 1)
    cur_week = _week_start(today)
    cur_month = today.replace(day=1)
    first_week = cur_week - timedelta(weeks=SUMMARY_WEEKS - 1)
    first_month = _shift_month(cur_month, -(SUMMARY_MONTHS - 1))
    earliest = min(chart_start, first_week, first_month)

    rows = await db.execute(
        select(TimeEntry.entry_date, func.coalesce(func.sum(TimeEntry.duration_minutes), 0))
        .where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.user_id == user_id,
            TimeEntry.entry_date >= earliest,
            TimeEntry.entry_date <= today,
        )
        .group_by(TimeEntry.entry_date)
    )
    per_day: dict[date, int] = {d: int(m or 0) for d, m in rows.all()}

    daily = [
        TimeDayOut(date=d, minutes=per_day.get(d, 0))
        for d in (chart_start + timedelta(days=i) for i in range(days))
    ]

    week_totals: dict[date, int] = {
        first_week + timedelta(weeks=i): 0 for i in range(SUMMARY_WEEKS)
    }
    month_totals: dict[date, int] = {
        _shift_month(first_month, i): 0 for i in range(SUMMARY_MONTHS)
    }
    for d, m in per_day.items():
        ws = _week_start(d)
        if ws in week_totals:
            week_totals[ws] += m
        ms = d.replace(day=1)
        if ms in month_totals:
            month_totals[ms] += m

    return MyTimeSummaryOut(
        today=today,
        week_start=cur_week,
        month_start=cur_month,
        today_minutes=per_day.get(today, 0),
        week_minutes=week_totals[cur_week],
        month_minutes=month_totals[cur_month],
        range_days=days,
        range_total_minutes=sum(x.minutes for x in daily),
        daily=daily,
        weekly=[TimeWeekOut(week_start=k, minutes=v) for k, v in week_totals.items()],
        monthly=[TimeMonthOut(month_start=k, minutes=v) for k, v in month_totals.items()],
    )


@router.get("/my/summary", response_model=MyTimeSummaryOut)
async def my_time_summary(
    days: int = Query(30, ge=7, le=90),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """The caller's OWN time history. Always scoped to the caller, for every
    role (a Team Lead opens a team member's via /user/{id}/summary)."""
    if not me.tenant_id:
        raise HTTPException(403, "Tenant context required")
    return await _build_time_summary(db, me.tenant_id, me.id, days)


@router.get("/user/{user_id}/summary", response_model=MyTimeSummaryOut)
async def user_time_summary(
    user_id: str,
    days: int = Query(30, ge=7, le=90),
    me: User = Depends(require_roles(Role.TEAM_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """A team member's time history, for their Team Lead — same shape as
    /my/summary. Team Leads only (Super Admins and Team Members get 403), and
    only for a Team Member inside the lead's report scope (or the lead
    themselves)."""
    if not me.tenant_id:
        raise HTTPException(403, "Tenant context required")

    target = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "User not found")
    if target.id != me.id:
        if target.role != Role.TEAM_MEMBER:
            raise HTTPException(
                403, "Time logs can only be viewed for team members"
            )
        await access.ensure_can_view_user(db, me, target.id)

    summary = await _build_time_summary(db, me.tenant_id, target.id, days)
    summary.user = UserMini(id=target.id, name=target.name, title=target.title)
    return summary


# ============================================================
# GET ONE
# ============================================================
@router.get("/{entry_id}", response_model=TimeEntryOut)
async def get_time_entry(
    entry_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    entry = await _load_entry_or_404(db, me.tenant_id or "", entry_id)
    await _ensure_can_view_entry(db, me, entry)
    return await _enrich_one(db, entry)


# ============================================================
# UPDATE
# ============================================================
@router.patch("/{entry_id}", response_model=TimeEntryOut)
async def update_time_entry(
    entry_id: str,
    payload: TimeEntryUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    entry = await _load_entry_or_404(db, me.tenant_id or "", entry_id)
    _ensure_can_mutate_entry(me, entry)

    changed: dict = {}
    if payload.entry_date is not None:
        entry.entry_date = payload.entry_date
        changed["entry_date"] = payload.entry_date.isoformat()
    if payload.started_at is not None:
        entry.started_at = payload.started_at
        changed["started_at"] = payload.started_at.isoformat()
    if payload.ended_at is not None:
        entry.ended_at = payload.ended_at
        changed["ended_at"] = payload.ended_at.isoformat()
    if payload.duration_minutes is not None:
        if payload.duration_minutes <= 0:
            raise HTTPException(400, "duration_minutes must be > 0")
        if payload.duration_minutes > 24 * 60:
            raise HTTPException(
                400, "duration_minutes cannot exceed 24 hours in a single entry"
            )
        entry.duration_minutes = payload.duration_minutes
        changed["duration_minutes"] = payload.duration_minutes
    if payload.note is not None:
        entry.note = payload.note
        changed["note"] = payload.note

    await db.flush()
    if changed:
        await log_action(
            db,
            tenant_id=entry.tenant_id,
            user_id=me.id,
            action="TIME_ENTRY_UPDATED",
            entity_type="TimeEntry",
            entity_id=entry.id,
            meta={"changes": changed},
        )
    await db.commit()
    await db.refresh(entry)
    await _notify_owner(entry.user_id, "time_entry:updated", entry)
    return await _enrich_one(db, entry)


# ============================================================
# DELETE
# ============================================================
@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_time_entry(
    entry_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    entry = await _load_entry_or_404(db, me.tenant_id or "", entry_id)
    _ensure_can_mutate_entry(me, entry)
    await log_action(
        db,
        tenant_id=entry.tenant_id,
        user_id=me.id,
        action="TIME_ENTRY_DELETED",
        entity_type="TimeEntry",
        entity_id=entry.id,
        meta={"duration_minutes": entry.duration_minutes},
    )
    owner_id = entry.user_id
    await db.delete(entry)
    await db.commit()
    # expire_on_commit=False (see database.py) keeps `entry`'s attributes
    # readable here even though the row is gone.
    await _notify_owner(owner_id, "time_entry:deleted", entry)
    return None


# ============================================================
# Internal guards
# ============================================================
async def _load_entry_or_404(
    db: AsyncSession, tenant_id: str, entry_id: str
) -> TimeEntry:
    row = await db.execute(
        select(TimeEntry).where(
            TimeEntry.id == entry_id, TimeEntry.tenant_id == tenant_id
        )
    )
    entry = row.scalar_one_or_none()
    if entry is None:
        raise HTTPException(404, "Time entry not found")
    return entry


async def _ensure_can_view_entry(db: AsyncSession, me: User, entry: TimeEntry) -> None:
    if entry.user_id == me.id:
        return
    if _is_admin(me):
        return
    await access.ensure_can_view_user(db, me, entry.user_id)
    await access.ensure_can_view_entity(db, me, entry.entity_id)


def _ensure_can_mutate_entry(me: User, entry: TimeEntry) -> None:
    if entry.user_id == me.id:
        return
    if me.role == Role.SUPER_ADMIN:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the owner of the entry or a Super Admin may modify it",
    )
