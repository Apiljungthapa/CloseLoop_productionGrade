"""KPI dashboards — Tenant / Entity / TeamLead."""

from datetime import date as _date_cls

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import (
    ClosePack,
    Entity,
    Period,
    PeriodStatus,
    Plan,
    PlanStatus,
    Priority,
    Role,
    SubTask,
    SubTaskEntityAssignee,
    Task,
    TaskEntityAssignee,
    TaskStatus,
    User,
)
from ..serialization import (
    subtask_entity_assignee_user_ids,
    subtask_entity_assignees_out,
)
from ..schemas import (
    EntityDashboardOut,
    LeadWorkbenchOut,
    LeadWorkbenchPlanOut,
    MyPeriodWorkOut,
    SubTaskOut,
    TaskBoardCountsOut,
    TaskBoardItemOut,
    TaskBoardOut,
    TaskEntityAssigneeOut,
    TaskOut,
    TeamLeadDashboardOut,
    TeamWorkloadMemberOut,
    TeamWorkloadOut,
    TenantDashboardOut,
    UserMini,
)
from ..auth import require_tenant
from ..calendar import nepal_today
from ..services.dashboard_service import (
    tenant_dashboard,
    entity_dashboard,
    team_lead_dashboard,
)
from ..services.period_service import get_current_period
from ..services.access_service import get_accessible_entity_ids
from ..services.optional_tasks import is_skipped, task_is_skipped
from ..services.plan_customization_service import (
    is_admin,
    is_team_lead,
)

# Statuses that count as "still open" for workload/progress purposes.
_OPEN_STATUSES = (
    TaskStatus.NOT_STARTED,
    TaskStatus.ASSIGNED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.REJECTED,
)

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


@router.get("/tenant", response_model=TenantDashboardOut)
async def tenant(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    if me.role != Role.SUPER_ADMIN:
        raise HTTPException(403, "Only Super Admin may view tenant KPIs")
    data = await tenant_dashboard(db, me.tenant_id)
    return TenantDashboardOut(**data)


@router.get("/entity/{entity_id}", response_model=EntityDashboardOut)
async def entity(
    entity_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    # Entity-wide KPI rollups (totals/overdue/pending-review across everyone
    # in the entity) are lead/admin-level oversight data, not an individual
    # contributor's own dashboard — a Team Member gets their own view via
    # GET /dashboards/me instead.
    if me.role == Role.TEAM_MEMBER:
        raise HTTPException(403, "Team Members do not have an entity-level dashboard")
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        accessible = await get_accessible_entity_ids(db, me)
        if entity_id not in accessible:
            raise HTTPException(403, "Entity not in your scope")
    try:
        data = await entity_dashboard(db, me.tenant_id, entity_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return EntityDashboardOut(**data)


@router.get("/team-lead/{user_id}", response_model=TeamLeadDashboardOut)
async def team_lead(
    user_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    if me.id != user_id and me.role != Role.SUPER_ADMIN:
        raise HTTPException(403, "You may only view your own dashboard")
    if me.id != user_id:
        target = (
            await db.execute(
                select(User.id).where(
                    User.id == user_id, User.tenant_id == me.tenant_id
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(404, "User not found")
    data = await team_lead_dashboard(db, me.tenant_id, user_id)
    return TeamLeadDashboardOut(**data)


@router.get("/me", response_model=TeamLeadDashboardOut)
async def my_dashboard(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    data = await team_lead_dashboard(db, me.tenant_id, me.id)
    return TeamLeadDashboardOut(**data)


# ------------------------------------------------------------------
# Unified per-user, per-period work view
# ------------------------------------------------------------------
async def _resolve_current_period(
    db: AsyncSession, tenant_id: str, period_id: str | None
) -> Period | None:
    """Resolve a period (by id) or pick the "current" one.

    Delegates to `period_service.get_current_period` — the single source
    of truth for "current period" shared by every API, the scheduler and
    the frontend's own mirror of this logic — rather than re-implementing
    the today/OPEN resolution here with its own (previously UTC-based)
    notion of "today".
    """
    if period_id:
        row = await db.execute(
            select(Period).where(Period.id == period_id, Period.tenant_id == tenant_id)
        )
        return row.scalar_one_or_none()

    period = await get_current_period(db, tenant_id)
    if period:
        return period

    # Legacy fallback: no OPEN/IN_PROGRESS period exists at all.
    row = await db.execute(
        select(Period)
        .where(Period.tenant_id == tenant_id)
        .order_by(Period.start_date.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _user_mini_map(
    db: AsyncSession, tenant_id: str, ids: set[str]
) -> dict[str, UserMini]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = await db.execute(
        select(User).where(User.id.in_(ids), User.tenant_id == tenant_id)
    )
    return {u.id: UserMini(id=u.id, name=u.name, title=u.title) for u in rows.scalars().all()}


def _serialize_subtask(
    s: SubTask,
    users: dict[str, UserMini],
    entity_names: dict[str, str] | None = None,
    calendar_type=None,
    plan_id=None,
    plan_title=None,
) -> SubTaskOut:
    return SubTaskOut(
        id=s.id,
        tenant_id=s.tenant_id,
        task_id=s.task_id,
        entity_id=s.entity_id,
        plan_id=plan_id,
        entity_name=(entity_names or {}).get(s.entity_id),
        calendar_type=calendar_type,
        plan_title=plan_title,
        source_template_id=s.source_template_id,
        is_custom=s.is_custom,
        assigned_to_id=s.assigned_to_id,
        assigned_entity_wide=s.assigned_entity_wide,
        entity_assignees=subtask_entity_assignees_out(s, users),
        reviewer_id=s.reviewer_id,
        title=s.title,
        description=s.description,
        due_date=s.due_date,
        due_date_type=s.due_date_type,
        post_period_due_days=s.post_period_due_days,
        occurrence_date=s.occurrence_date,
        due_days=s.due_days,
        due_time=s.due_time,
        priority=s.priority,
        tags=s.tags,
        status=s.status,
        rejection_note=s.rejection_note,
        completion_notes=s.completion_notes,
        completion_time_minutes=s.completion_time_minutes,
        submitted_by_id=s.submitted_by_id,
        submitted_at=s.submitted_at,
        completed_at=getattr(s, "completed_at", None),
        created_at=s.created_at,
        carried_from_label=getattr(s, "carried_from_label", None),
        assigned_to=users.get(s.assigned_to_id) if s.assigned_to_id else None,
        reviewer=users.get(s.reviewer_id) if s.reviewer_id else None,
        submitted_by=users.get(s.submitted_by_id) if s.submitted_by_id else None,
    )


def _serialize_task(
    t: Task,
    users: dict[str, UserMini],
    entity_names: dict[str, str] | None = None,
    plan_info: dict[str, dict] | None = None,
) -> TaskOut:
    info = (plan_info or {}).get(t.plan_id) or {}
    calendar_type = info.get("calendar_type")
    plan_title = info.get("title")
    return TaskOut(
        id=t.id,
        tenant_id=t.tenant_id,
        plan_id=t.plan_id,
        entity_id=t.entity_id,
        entity_name=(entity_names or {}).get(t.entity_id),
        calendar_type=calendar_type,
        plan_title=plan_title,
        source_template_id=t.source_template_id,
        recurring_group_id=t.recurring_group_id,
        is_custom=t.is_custom,
        is_required=t.is_required,
        assigned_to_id=t.assigned_to_id,
        member_assigned_to_id=t.member_assigned_to_id,
        assigned_entity_wide=t.assigned_entity_wide,
        title=t.title,
        description=t.description,
        due_date=t.due_date,
        due_date_type=t.due_date_type,
        post_period_due_days=t.post_period_due_days,
        occurrence_date=t.occurrence_date,
        due_days=t.due_days,
        due_time=t.due_time,
        priority=t.priority,
        tags=t.tags,
        status=t.status,
        rejection_note=t.rejection_note,
        completion_notes=t.completion_notes,
        completion_time_minutes=t.completion_time_minutes,
        submitted_by_id=t.submitted_by_id,
        submitted_at=t.submitted_at,
        completed_at=getattr(t, "completed_at", None),
        created_at=t.created_at,
        carried_from_label=getattr(t, "carried_from_label", None),
        assigned_to=users.get(t.assigned_to_id) if t.assigned_to_id else None,
        member_assigned_to=(
            users.get(t.member_assigned_to_id) if t.member_assigned_to_id else None
        ),
        submitted_by=users.get(t.submitted_by_id) if t.submitted_by_id else None,
        entity_assignees=[
            TaskEntityAssigneeOut(
                id=a.id,
                task_id=a.task_id,
                user_id=a.user_id,
                done=a.done,
                done_at=a.done_at,
                due_date=a.due_date,
                completion_notes=a.completion_notes,
                completion_time_minutes=a.completion_time_minutes,
                created_at=a.created_at,
                user=users.get(a.user_id),
            )
            for a in t.entity_assignees
        ],
        subtasks=[
            _serialize_subtask(s, users, entity_names, calendar_type, t.plan_id, plan_title)
            for s in t.sub_tasks
        ],
    )


async def _plan_info_map(
    db: AsyncSession, tenant_id: str, plan_ids: set[str] | list[str]
) -> dict[str, dict]:
    """plan_id -> {"title": Plan.title, "calendar_type": owning Pack's
    AD/BS}, for stamping TaskOut/SubTaskOut.plan_title / .calendar_type in
    views that span multiple plans (e.g. GET /dashboards/me/period-work).
    A single source of truth so no call site ever has to fall back to
    entity_name (or anything else) as a stand-in for the Pack's own name.
    Outer join: a plan whose source Pack was since deleted still has its
    own title, just no calendar_type.
    """
    plan_ids = {p for p in plan_ids if p}
    if not plan_ids:
        return {}
    rows = await db.execute(
        select(Plan.id, Plan.title, ClosePack.calendar_type)
        .outerjoin(ClosePack, Plan.source_pack_id == ClosePack.id)
        .where(Plan.tenant_id == tenant_id, Plan.id.in_(plan_ids))
    )
    return {pid: {"title": title, "calendar_type": cal} for pid, title, cal in rows.all()}


@router.get("/me/period-work", response_model=MyPeriodWorkOut)
async def my_period_work(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
    user_id: str | None = None,
):
    """
    Unified per-user work view for one period.

    Combines TEMPLATE-sourced items and custom items added by a Team Lead
    on the plan — the `is_custom` flag on each item lets the UI distinguish
    them if needed. Visibility follows Tier-3 rules:

      * `tasks`    — tasks where `assigned_to_id == me.id`
      * `subtasks` — subtasks where `assigned_to_id == me.id` OR
                     `reviewer_id == me.id`

    The same item may appear in multiple lists (e.g. a task assigned to
    me has its subtasks nested, but those subtasks ALSO show in the
    `subtasks` array when I'm directly assigned). Clients should dedupe
    by `id` where required.

    `user_id` (optional) — a Team Lead or Admin may pass another user's id
    to see that member's work view instead of their own (drives the
    "member tasks" page opened from the Team Workload panel). `period_id`
    may reference a CLOSED period for a historical summary.
    """
    target_id = me.id
    if user_id and user_id != me.id:
        if not (is_admin(me) or is_team_lead(me)):
            raise HTTPException(
                403, "Only a Team Lead or Admin may view another member's work"
            )
        target = (
            await db.execute(
                select(User).where(
                    User.id == user_id, User.tenant_id == me.tenant_id
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(404, "User not found")
        target_id = target.id

    period = await _resolve_current_period(db, me.tenant_id, period_id)
    if not period:
        raise HTTPException(404, "No matching period")

    # Find all plans for this period (tenant-scoped)
    plan_rows = await db.execute(
        select(Plan.id).where(
            Plan.tenant_id == me.tenant_id, Plan.period_id == period.id
        )
    )
    plan_ids = [pid for (pid,) in plan_rows.all()]
    if not plan_ids:
        return MyPeriodWorkOut(
            period_id=period.id,
            period_label=period.label,
            tasks=[],
            subtasks=[],
        )

    # Tasks directly assigned to me in this period — eager-load subtasks so
    # the response is a complete drill-down tree. Matches the (legacy)
    # direct assignee slot on an ad-hoc task, member_assigned_to_id on a
    # leaf task a Lead handed to me, or one of the entity-wide
    # TaskEntityAssignee rows fanned out to my entity.
    my_entity_task_rows = await db.execute(
        select(TaskEntityAssignee.task_id).where(
            TaskEntityAssignee.tenant_id == me.tenant_id,
            TaskEntityAssignee.user_id == target_id,
        )
    )
    my_entity_task_ids = {r[0] for r in my_entity_task_rows.all()}
    my_task_conditions = [
        Task.assigned_to_id == target_id,
        Task.member_assigned_to_id == target_id,
    ]
    if my_entity_task_ids:
        my_task_conditions.append(Task.id.in_(my_entity_task_ids))
    task_rows = await db.execute(
        select(Task)
        .where(
            Task.tenant_id == me.tenant_id,
            Task.plan_id.in_(plan_ids),
            or_(*my_task_conditions),
        )
        .options(
            selectinload(Task.entity_assignees),
            selectinload(Task.sub_tasks),
        )
        .order_by(Task.due_date.is_(None), Task.due_date, Task.created_at)
    )
    my_tasks = list(task_rows.scalars().all())

    # Subtasks where I'm assignee or reviewer (may live under tasks NOT
    # assigned to me — that's fine; the lead may delegate just the subtask).
    my_shared_sub_ids = select(SubTaskEntityAssignee.subtask_id).where(
        SubTaskEntityAssignee.tenant_id == me.tenant_id,
        SubTaskEntityAssignee.user_id == target_id,
    )
    sub_rows = await db.execute(
        select(SubTask, Task.plan_id)
        .join(Task, SubTask.task_id == Task.id)
        .where(
            SubTask.tenant_id == me.tenant_id,
            Task.plan_id.in_(plan_ids),
            or_(
                SubTask.assigned_to_id == target_id,
                SubTask.reviewer_id == target_id,
                SubTask.id.in_(my_shared_sub_ids),
            ),
        )
        .options(selectinload(SubTask.entity_assignees))
        .order_by(SubTask.due_date.is_(None), SubTask.due_date, SubTask.created_at)
    )
    # Subtasks surface here independently of their parent task (a Lead may
    # delegate just the subtask), so each one's own plan_id — not
    # necessarily one of my_tasks' plans — must be tracked separately for
    # calendar_type resolution below.
    sub_rows_all = sub_rows.all()
    my_subs = [s for s, _pid in sub_rows_all]
    sub_plan_id = {s.id: pid for s, pid in sub_rows_all}

    # Collect every user_id we will need for embedded UserMini
    uids: set[str] = set()
    for t in my_tasks:
        if t.assigned_to_id:
            uids.add(t.assigned_to_id)
        if t.member_assigned_to_id:
            uids.add(t.member_assigned_to_id)
        if t.submitted_by_id:
            uids.add(t.submitted_by_id)
        for a in t.entity_assignees:
            uids.add(a.user_id)
        for s in t.sub_tasks:
            if s.assigned_to_id:
                uids.add(s.assigned_to_id)
            if s.reviewer_id:
                uids.add(s.reviewer_id)
            if s.submitted_by_id:
                uids.add(s.submitted_by_id)
    for s in my_subs:
        if s.assigned_to_id:
            uids.add(s.assigned_to_id)
        if s.reviewer_id:
            uids.add(s.reviewer_id)
        if s.submitted_by_id:
            uids.add(s.submitted_by_id)
        uids |= subtask_entity_assignee_user_ids(s)

    users = await _user_mini_map(db, me.tenant_id, uids)

    # Entity names — a member's own work can span several entities
    # (primary + secondary assignments), unlike the Lead's task board
    # which is already scoped to one entity at a time.
    entity_ids = {t.entity_id for t in my_tasks} | {s.entity_id for s in my_subs}
    entity_names: dict[str, str] = {}
    if entity_ids:
        ent_rows = await db.execute(
            select(Entity.id, Entity.name).where(
                Entity.id.in_(entity_ids), Entity.tenant_id == me.tenant_id
            )
        )
        entity_names = {eid: name for eid, name in ent_rows.all()}

    plan_info = await _plan_info_map(
        db, me.tenant_id, {t.plan_id for t in my_tasks} | set(sub_plan_id.values())
    )

    return MyPeriodWorkOut(
        period_id=period.id,
        period_label=period.label,
        tasks=[_serialize_task(t, users, entity_names, plan_info) for t in my_tasks],
        subtasks=[
            _serialize_subtask(
                s, users, entity_names,
                (plan_info.get(sub_plan_id.get(s.id)) or {}).get("calendar_type"),
                sub_plan_id.get(s.id),
                (plan_info.get(sub_plan_id.get(s.id)) or {}).get("title"),
            )
            for s in my_subs
        ],
    )


# ------------------------------------------------------------------
# Team-Lead structural workbench
# ------------------------------------------------------------------
async def _resolve_open_period(
    db: AsyncSession, tenant_id: str, period_id: str | None
) -> Period | None:
    """Resolve the requested period (by id) or pick the "current" one.

    Delegates to `period_service.get_current_period` (same resolution
    order this used to duplicate inline: period containing today, else
    earliest OPEN/IN_PROGRESS) so every dashboard endpoint agrees with the
    scheduler and `/periods/current` on what "today" and "current" mean.
    """
    if period_id:
        row = await db.execute(
            select(Period).where(
                Period.id == period_id, Period.tenant_id == tenant_id
            )
        )
        return row.scalar_one_or_none()

    return await get_current_period(db, tenant_id)


@router.get("/me/lead-workbench", response_model=LeadWorkbenchOut)
async def my_lead_workbench(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
):
    """
    Team-Lead structural workbench for an OPEN period.

    Returns every plan the caller may structurally edit for the resolved
    period, each drilled-down to its full task → subtask tree. For a
    Team Lead that's ownership-only (`Plan.owner_id == me.id`) — a plan
    generated but not (yet) assigned to them doesn't show up here at all;
    browse it read-only instead via `GET /api/plans` / `GET /api/plans/{id}`.
    Template items and custom items (`is_custom=true`) are mixed in the
    same tree; the flag is preserved on each item so the UI can badge them.

    Permission: Admin (full tenant view), Team Lead (their owned plans
    only). Members get 403 — they have `/me/period-work` instead.
    """
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(
            403, "Lead workbench is for Team Leads and Admins only"
        )

    period = await _resolve_open_period(db, me.tenant_id, period_id)
    if not period:
        raise HTTPException(404, "No matching open period")

    my_plans = await _resolve_lead_plans(db, me, period=period)

    if not my_plans:
        return LeadWorkbenchOut(
            period_id=period.id, period_label=period.label, plans=[]
        )

    plan_ids = [p.id for p in my_plans]

    # Eager-load full task tree for those plans
    task_rows = await db.execute(
        select(Task)
        .where(Task.tenant_id == me.tenant_id, Task.plan_id.in_(plan_ids))
        .options(
            selectinload(Task.entity_assignees),
            selectinload(Task.sub_tasks),
        )
        .order_by(Task.plan_id, Task.due_date.is_(None), Task.due_date, Task.created_at)
    )
    tasks = list(task_rows.scalars().all())

    # Group tasks by plan
    tasks_by_plan: dict[str, list[Task]] = {}
    for t in tasks:
        tasks_by_plan.setdefault(t.plan_id, []).append(t)

    # Look up entity names
    ent_rows = await db.execute(
        select(Entity.id, Entity.name).where(
            Entity.id.in_({p.entity_id for p in my_plans}),
            Entity.tenant_id == me.tenant_id,
        )
    )
    ent_names = {eid: name for eid, name in ent_rows.all()}

    # Collect every user_id we'll embed
    uids: set[str] = set()
    for p in my_plans:
        if p.owner_id:
            uids.add(p.owner_id)
    for t in tasks:
        if t.assigned_to_id:
            uids.add(t.assigned_to_id)
        if t.member_assigned_to_id:
            uids.add(t.member_assigned_to_id)
        if t.submitted_by_id:
            uids.add(t.submitted_by_id)
        for a in t.entity_assignees:
            uids.add(a.user_id)
        for s in t.sub_tasks:
            if s.assigned_to_id:
                uids.add(s.assigned_to_id)
            if s.reviewer_id:
                uids.add(s.reviewer_id)
            if s.submitted_by_id:
                uids.add(s.submitted_by_id)
    users = await _user_mini_map(db, me.tenant_id, uids)

    plans_out: list[LeadWorkbenchPlanOut] = []
    for p in my_plans:
        plans_out.append(
            LeadWorkbenchPlanOut(
                id=p.id,
                tenant_id=p.tenant_id,
                period_id=p.period_id,
                entity_id=p.entity_id,
                owner_id=p.owner_id,
                title=p.title,
                description=p.description,
                status=p.status,
                due_date=p.due_date,
                entity_name=ent_names.get(p.entity_id),
                tasks=[
                    _serialize_task(t, users) for t in tasks_by_plan.get(p.id, [])
                ],
            )
        )

    return LeadWorkbenchOut(
        period_id=period.id,
        period_label=period.label,
        plans=plans_out,
    )


# ------------------------------------------------------------------
# Shared scoping helper for the endpoints below
# ------------------------------------------------------------------
async def _resolve_lead_plans(
    db: AsyncSession,
    me: User,
    *,
    period: Period,
    entity_id: str | None = None,
) -> list[Plan]:
    """Plans in `period` that `me` (Admin or Team Lead) may manage,
    optionally narrowed to one entity. For a Team Lead that's
    ownership-only (`Plan.owner_id == me.id`) — matches
    `can_lead_edit_plan` in `plan_customization_service.py`, kept in sync
    deliberately. A plan not assigned to them doesn't show up in their
    workbench at all; they browse it read-only via `GET /api/plans`."""
    plan_filters = [Plan.tenant_id == me.tenant_id, Plan.period_id == period.id]
    if entity_id:
        plan_filters.append(Plan.entity_id == entity_id)
    plan_rows = await db.execute(select(Plan).where(*plan_filters))
    all_plans = list(plan_rows.scalars().all())

    if is_admin(me):
        return all_plans

    return [p for p in all_plans if p.owner_id == me.id]


def _bucket_for_task(task: Task, today: _date_cls) -> str:
    """Classify a task into exactly one status bucket — the six buckets
    partition every task with no overlap, so their counts always sum to
    the total (matches the "All Tasks" tab count on the task board)."""
    if task.status == TaskStatus.COMPLETED:
        return "completed"
    if task.status == TaskStatus.PENDING_REVIEW:
        return "pending_review"
    # Skipped work (optional, past due, not done) never reaches this function
    # when counting — callers drop it first — so a past-due task here is overdue.
    if task.due_date is not None and task.due_date < today:
        return "overdue"
    if task.due_date is not None and task.due_date == today:
        return "due_today"
    if task.status == TaskStatus.IN_PROGRESS:
        return "in_progress"
    return "not_started"


# ------------------------------------------------------------------
# Team workload — open task load per person, for the Lead/Admin
# ------------------------------------------------------------------
@router.get("/team-workload", response_model=TeamWorkloadOut)
async def team_workload(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
):
    """
    Per-person open-work load for a period — "how much of what's on this
    person's plate is still outstanding". Counts every leaf work item
    (a Task with no sub-tasks, or a SubTask) assigned to each person;
    `load_pct` = their open count / their total count. Independent per
    person (does not sum to 100% across the team) — >80% is flagged.

    Permission: Admin (tenant-wide) or Team Lead (their plans only).
    """
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(
            403, "Team workload is for Team Leads and Admins only"
        )

    period = await _resolve_open_period(db, me.tenant_id, period_id)
    if not period:
        raise HTTPException(404, "No matching open period")

    my_plans = await _resolve_lead_plans(db, me, period=period)
    if not my_plans:
        return TeamWorkloadOut(period_id=period.id, period_label=period.label, members=[])

    plan_ids = [p.id for p in my_plans]
    task_rows = await db.execute(
        select(Task)
        .where(Task.tenant_id == me.tenant_id, Task.plan_id.in_(plan_ids))
        .options(
            selectinload(Task.entity_assignees),
            selectinload(Task.sub_tasks),
        )
    )
    tasks = list(task_rows.scalars().all())

    # (user_id, status) for every leaf work item — a task WITH sub-tasks
    # contributes nothing itself (its sub-tasks are the real work items);
    # a leaf task (no sub-tasks) contributes once per executor.
    today = nepal_today()
    entries: list[tuple[str, TaskStatus]] = []
    for t in tasks:
        if t.sub_tasks:
            for s in t.sub_tasks:
                # A sub-task inherits its parent's optional flag.
                if s.assigned_to_id and not is_skipped(
                    is_required=t.is_required, status=s.status,
                    due_date=s.due_date, today=today,
                ):
                    entries.append((s.assigned_to_id, s.status))
            continue
        if task_is_skipped(t, today):
            continue  # skipped work is not part of anyone's counted load
        if t.assigned_entity_wide:
            for a in t.entity_assignees:
                entries.append(
                    (a.user_id, TaskStatus.COMPLETED if a.done else t.status)
                )
        else:
            uid = t.member_assigned_to_id or t.assigned_to_id
            if uid:
                entries.append((uid, t.status))

    totals: dict[str, int] = {}
    opens: dict[str, int] = {}
    for uid, status in entries:
        totals[uid] = totals.get(uid, 0) + 1
        if status in _OPEN_STATUSES:
            opens[uid] = opens.get(uid, 0) + 1

    if not totals:
        return TeamWorkloadOut(period_id=period.id, period_label=period.label, members=[])

    user_rows = await db.execute(
        select(User).where(User.id.in_(totals.keys()), User.tenant_id == me.tenant_id)
    )
    users_by_id = {u.id: u for u in user_rows.scalars().all()}

    members: list[TeamWorkloadMemberOut] = []
    for uid, total in totals.items():
        u = users_by_id.get(uid)
        if not u:
            continue  # stale assignment to a deleted/other-tenant user
        open_count = opens.get(uid, 0)
        load_pct = round((open_count / total) * 100, 1) if total else 0.0
        members.append(
            TeamWorkloadMemberOut(
                user_id=u.id,
                name=u.name,
                role=u.role,
                open_count=open_count,
                total_count=total,
                load_pct=load_pct,
                overloaded=load_pct > 80,
            )
        )
    members.sort(key=lambda m: m.name.lower())

    return TeamWorkloadOut(
        period_id=period.id, period_label=period.label, members=members
    )


# ------------------------------------------------------------------
# Task board — entity/period-wise task+subtask browser with status buckets
# ------------------------------------------------------------------
@router.get("/task-board", response_model=TaskBoardOut)
async def task_board(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
    entity_id: str | None = None,
    scope: str = Query("team", pattern="^(team|mine)$"),
    status_bucket: str | None = Query(
        None,
        pattern="^(not_started|in_progress|due_today|overdue|completed|pending_review)$",
    ),
    priority: Priority | None = None,
    tag: str | None = None,
    search: str | None = None,
    sort: str = Query("due_date", pattern="^(due_date|priority|status|title|created_at)$"),
):
    """
    Entity- and period-wise task browser for the Lead/Admin workbench.

    Rows are Tasks (each carrying its full `subtasks` array nested — a
    task board row is never a bare sub-task). `entity_id` narrows to one
    entity's plan for that period; omit it to span every entity in scope
    (matches an "All Entities" view). `counts` always reflects the full
    set for the resolved entity/period scope — independent of
    `status_bucket`/`priority`/`search`, which only filter `tasks` — so
    tab badges stay stable while the list itself narrows.

    Permission: Admin (tenant-wide) or Team Lead (their plans only).
    """
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Task board is for Team Leads and Admins only")

    period = await _resolve_open_period(db, me.tenant_id, period_id)
    if not period:
        raise HTTPException(404, "No matching open period")

    if entity_id and not is_admin(me):
        owned_entity_rows = await db.execute(
            select(Plan.entity_id).where(
                Plan.tenant_id == me.tenant_id, Plan.owner_id == me.id
            )
        )
        owned_entities = {e for (e,) in owned_entity_rows.all()}
        if entity_id not in owned_entities:
            raise HTTPException(
                403,
                "You don't own a plan for this entity — browse it "
                "read-only via GET /api/plans instead.",
            )

    my_plans = await _resolve_lead_plans(db, me, period=period, entity_id=entity_id)

    empty = TaskBoardOut(
        period_id=period.id,
        period_label=period.label,
        entity_id=entity_id,
        counts=TaskBoardCountsOut(),
    )
    if not my_plans:
        return empty

    plan_ids = [p.id for p in my_plans]
    task_rows = await db.execute(
        select(Task)
        .where(Task.tenant_id == me.tenant_id, Task.plan_id.in_(plan_ids))
        .options(
            selectinload(Task.entity_assignees),
            selectinload(Task.sub_tasks),
        )
    )
    tasks = list(task_rows.scalars().all())

    if scope == "mine":
        def _is_mine(t: Task) -> bool:
            if t.assigned_to_id == me.id or t.member_assigned_to_id == me.id:
                return True
            if any(a.user_id == me.id for a in t.entity_assignees):
                return True
            if any(s.assigned_to_id == me.id for s in t.sub_tasks):
                return True
            return False

        tasks = [t for t in tasks if _is_mine(t)]

    today = nepal_today()
    # Skipped tasks (optional, past due, not done) stay in the list below but
    # are never added to the totals, the tab counts or the progress figure.
    counted_tasks = [t for t in tasks if not task_is_skipped(t, today)]
    counts = TaskBoardCountsOut(all=len(counted_tasks))
    for t in counted_tasks:
        bucket = _bucket_for_task(t, today)
        setattr(counts, bucket, getattr(counts, bucket) + 1)

    filtered = tasks
    if status_bucket:
        filtered = [t for t in filtered if _bucket_for_task(t, today) == status_bucket]
    if priority:
        filtered = [t for t in filtered if t.priority == priority]
    if tag:
        needle = tag.strip().lower()
        filtered = [
            t
            for t in filtered
            if needle in (x.lower() for x in t.tags)
            or any(needle in (x.lower() for x in s.tags) for s in t.sub_tasks)
        ]
    if search:
        needle = search.strip().lower()
        filtered = [
            t
            for t in filtered
            if needle in (t.title or "").lower()
            or needle in (t.description or "").lower()
        ]

    _PRIORITY_RANK = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
    _STATUS_RANK = {
        TaskStatus.PENDING_REVIEW: 0,
        TaskStatus.IN_PROGRESS: 1,
        TaskStatus.ASSIGNED: 2,
        TaskStatus.NOT_STARTED: 3,
        TaskStatus.REJECTED: 4,
        TaskStatus.CARRIED_FORWARD: 5,
        TaskStatus.COMPLETED: 6,
    }
    if sort == "priority":
        filtered.sort(key=lambda t: _PRIORITY_RANK.get(t.priority, 9))
    elif sort == "status":
        filtered.sort(key=lambda t: _STATUS_RANK.get(t.status, 9))
    elif sort == "title":
        filtered.sort(key=lambda t: (t.title or "").lower())
    elif sort == "created_at":
        filtered.sort(key=lambda t: t.created_at, reverse=True)
    else:  # due_date — soonest first, undated last
        filtered.sort(key=lambda t: (t.due_date is None, t.due_date))

    # Enrich: entity names, user minis
    ent_rows = await db.execute(
        select(Entity.id, Entity.name).where(
            Entity.id.in_({p.entity_id for p in my_plans}),
            Entity.tenant_id == me.tenant_id,
        )
    )
    ent_names = {eid: name for eid, name in ent_rows.all()}
    plans_by_id = {p.id: p for p in my_plans}

    uids: set[str] = set()
    for t in filtered:
        if t.assigned_to_id:
            uids.add(t.assigned_to_id)
        if t.member_assigned_to_id:
            uids.add(t.member_assigned_to_id)
        for s in t.sub_tasks:
            if s.assigned_to_id:
                uids.add(s.assigned_to_id)
            if s.reviewer_id:
                uids.add(s.reviewer_id)
    users = await _user_mini_map(db, me.tenant_id, uids)

    items: list[TaskBoardItemOut] = []
    for t in filtered:
        plan = plans_by_id.get(t.plan_id)
        sub_done = sum(1 for s in t.sub_tasks if s.status == TaskStatus.COMPLETED)
        sub_total = len(t.sub_tasks)
        progress_pct = round((sub_done / sub_total) * 100, 1) if sub_total else (
            100.0 if t.status == TaskStatus.COMPLETED else 0.0
        )
        items.append(
            TaskBoardItemOut(
                item_type="TASK",
                id=t.id,
                plan_id=t.plan_id,
                entity_id=t.entity_id,
                entity_name=ent_names.get(t.entity_id),
                title=t.title,
                description=t.description,
                is_custom=t.is_custom,
                is_required=t.is_required,
                assigned_to_id=t.assigned_to_id,
                assigned_to=users.get(t.assigned_to_id) if t.assigned_to_id else None,
                due_date=t.due_date,
                due_date_type=t.due_date_type,
                post_period_due_days=t.post_period_due_days,
                occurrence_date=t.occurrence_date,
                due_days=t.due_days,
                priority=t.priority,
                tags=t.tags,
                status=t.status,
                bucket=_bucket_for_task(t, today),
                subtask_count=sub_total,
                subtask_done_count=sub_done,
                progress_pct=progress_pct,
                subtasks=[_serialize_subtask(s, users) for s in t.sub_tasks],
            )
        )

    single_plan = my_plans[0] if (entity_id and len(my_plans) == 1) else None
    total_count = len(counted_tasks)
    completed_count = sum(1 for t in counted_tasks if t.status == TaskStatus.COMPLETED)

    return TaskBoardOut(
        period_id=period.id,
        period_label=period.label,
        entity_id=entity_id,
        entity_name=ent_names.get(entity_id) if entity_id else None,
        plan_id=single_plan.id if single_plan else None,
        plan_title=single_plan.title if single_plan else None,
        plan_due_date=single_plan.due_date if single_plan else None,
        total_count=total_count,
        completed_count=completed_count,
        progress_pct=round((completed_count / total_count) * 100, 1) if total_count else 0.0,
        counts=counts,
        tasks=items,
    )


@router.get("/tags", response_model=list[str])
async def list_tags(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Every distinct tag currently in use across the tenant's tasks and
    sub-tasks, alphabetically — feeds the task board's tag filter
    dropdown. Admin/Lead only, same bar as the task board itself."""
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Tags are visible to Team Leads and Admins only")

    task_tag_rows = await db.execute(
        select(Task.tags).where(Task.tenant_id == me.tenant_id)
    )
    sub_tag_rows = await db.execute(
        select(SubTask.tags).where(SubTask.tenant_id == me.tenant_id)
    )
    tag_set: set[str] = set()
    for (tags,) in task_tag_rows.all():
        tag_set.update(tags or [])
    for (tags,) in sub_tag_rows.all():
        tag_set.update(tags or [])
    return sorted(tag_set, key=str.lower)
