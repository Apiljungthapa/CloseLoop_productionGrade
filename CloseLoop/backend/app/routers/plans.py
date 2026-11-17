"""Plans — generated from Close Packs only.

GET /plans returns enriched aggregates (task_count, completed_count,
progress, type, period_label, entity_name) for tabular display.

GET /plans/{id} returns the full nested tree (tasks → subtasks) with
embedded UserMini for assigned_to / reviewer / submitted_by so the
frontend never has to make N+1 user lookups.
"""

from __future__ import annotations
from datetime import date
from typing import Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..calendar import nepal_today
from ..database import get_db
from ..models import (
    Plan,
    Task,
    SubTask,
    SubTaskEntityAssignee,
    TaskEntityAssignee,
    User,
    Role,
    ClosePack,
    Period,
    Entity,
    Priority,
    TaskStatus,
)
from ..schemas import (
    PlanOut,
    PlanGenerateIn,
    PlanGenerateOut,
    PlanUpdate,
    PlanOwnerAssignIn,
    PlanPreviewOut,
    TaskOut,
    TaskEntityAssigneeOut,
    SubTaskOut,
    UserMini,
)
from ..auth import require_tenant, require_roles
from ..serialization import (
    subtask_entity_assignee_user_ids,
    subtask_entity_assignees_out,
)
from ..services.plan_generation_service import (
    generate_plans,
    preview_custom_recurring_for_period,
    preview_tasks_for_period,
)
from ..services.close_pack_service import load_task_templates, build_snapshot_payload
from ..services.access_service import get_accessible_entity_ids
from ..services.optional_tasks import counted_clause
from ..services.audit_service import log_action
from ..services.plan_customization_service import (
    can_lead_edit_plan,
    ensure_can_assign_plan_owner,
    ensure_lead_edit_plan,
)

router = APIRouter(prefix="/api/plans", tags=["plans"])


async def _build_user_mini_map(
    db: AsyncSession, tenant_id: str, user_ids: set[str]
) -> dict[str, UserMini]:
    """Single query to fetch UserMini for a batch of user IDs, scoped to
    `tenant_id` like every other batch-lookup helper in this router."""
    user_ids = {uid for uid in user_ids if uid}
    if not user_ids:
        return {}
    rows = await db.execute(
        select(User.id, User.name, User.title).where(
            User.id.in_(user_ids), User.tenant_id == tenant_id
        )
    )
    return {
        uid: UserMini(id=uid, name=name, title=title) for uid, name, title in rows.all()
    }


def _serialize_subtask(s: SubTask, users: dict[str, UserMini]) -> SubTaskOut:
    return SubTaskOut(
        id=s.id,
        tenant_id=s.tenant_id,
        task_id=s.task_id,
        entity_id=s.entity_id,
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


_PRIORITY_RANK = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}


def _deadline_key(due: Optional[date], today: date) -> tuple[int, int]:
    """Where a due date sits in a task list, as (group, distance):

      group 0  due today or later - distance = days away, so today comes
               first, then tomorrow, then further out
      group 1  no due date
      group 2  deadline already missed - distance = days ago, so the most
               recently missed comes first and the oldest sinks furthest

    `today` is Nepal's date (nepal_today), the same reference the "Due
    today / Due tomorrow / overdue" labels use. The frontend's
    sortByDeadline (leadDashUtils.js) is the mirror of this.
    """
    if due is None:
        return (1, 0)
    days = (due - today).days
    return (0, days) if days >= 0 else (2, -days)


def _task_order(t: Task, today: date) -> tuple:
    """Sort key for a pack's tasks: due today first, upcoming deadlines next
    (soonest first), missed ones at the bottom. Tasks due the same day put
    higher priority first, then fall back to creation order."""
    return (
        *_deadline_key(t.due_date, today),
        _PRIORITY_RANK.get(t.priority, len(_PRIORITY_RANK)),
        t.created_at,
        t.title,
    )


def _subtask_order(s: SubTask, today: date) -> tuple:
    """Same reading order for a task's sub-tasks. Ties keep creation order
    (not priority) so steps due the same day stay in the order they were
    authored."""
    return (*_deadline_key(s.due_date, today), s.created_at, s.title)


def _serialize_task(t: Task, users: dict[str, UserMini]) -> TaskOut:
    return TaskOut(
        id=t.id,
        tenant_id=t.tenant_id,
        plan_id=t.plan_id,
        entity_id=t.entity_id,
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
        subtasks=[_serialize_subtask(s, users) for s in t.sub_tasks],
    )


def _collect_user_ids(plans: Sequence[Plan]) -> set[str]:
    ids: set[str] = set()
    for p in plans:
        if p.owner_id:
            ids.add(p.owner_id)
        for t in p.tasks:
            if t.assigned_to_id:
                ids.add(t.assigned_to_id)
            if t.member_assigned_to_id:
                ids.add(t.member_assigned_to_id)
            if t.submitted_by_id:
                ids.add(t.submitted_by_id)
            for a in t.entity_assignees:
                ids.add(a.user_id)
            for s in t.sub_tasks:
                if s.assigned_to_id:
                    ids.add(s.assigned_to_id)
                if s.reviewer_id:
                    ids.add(s.reviewer_id)
                if s.submitted_by_id:
                    ids.add(s.submitted_by_id)
                ids |= subtask_entity_assignee_user_ids(s)
    return ids


async def _aggregate_for_plans(
    db: AsyncSession, tenant_id: str, plan_ids: list[str], *, exclude_custom: bool = False
) -> dict[str, tuple[int, int, int]]:
    """Return {plan_id: (task_count, completed_count, pending_review_count)}.

    `pending_review_count` counts Tasks whose status is PENDING_REVIEW —
    since a parent task's status rolls up from its sub-tasks (see
    rollup.py), a task with any sub-task pending review is itself
    PENDING_REVIEW, so this single count covers leaf tasks and
    sub-tasked tasks alike. Feeds the pack card's "pending review" badge
    so a Team Lead can see at a glance which packs need their attention.

    `exclude_custom` — Super Admin/Platform Owner's pack view shows only
    what the Close Pack template actually generated; a Team Lead's own
    ad-hoc additions (Task.is_custom) stay visible to the Lead (and any
    Member assigned to one) but don't count toward the Admin-facing
    totals or progress bar.
    """
    if not plan_ids:
        return {}
    # UPCOMING tasks (locked future-period instances + "scheduled"
    # placeholders for sparse recurrences) aren't live work for this
    # plan, so they don't count toward its totals / progress bar.
    # SKIPPED tasks (optional, past due, not done — see optional_tasks) are
    # visible in the plan but add to no total, completed count or progress.
    # Every other optional task counts like a required one.
    filters = [
        Task.tenant_id == tenant_id,
        Task.plan_id.in_(plan_ids),
        Task.status != TaskStatus.UPCOMING,
        counted_clause(nepal_today()),
    ]
    if exclude_custom:
        filters.append(Task.is_custom.is_(False))
    rows = await db.execute(
        select(
            Task.plan_id,
            func.count(Task.id).label("total"),
            func.sum(
                func.cast((Task.status == TaskStatus.COMPLETED), type_=Integer)
            ).label("done"),
            func.sum(
                func.cast((Task.status == TaskStatus.PENDING_REVIEW), type_=Integer)
            ).label("pending_review"),
        )
        .where(*filters)
        .group_by(Task.plan_id)
    )
    return {
        pid: (int(total or 0), int(done or 0), int(pending_review or 0))
        for pid, total, done, pending_review in rows.all()
    }


async def _build_pack_type_map(
    db: AsyncSession, tenant_id: str, pack_ids: set[str]
) -> dict[str, str]:
    """Despite the name (kept to avoid touching every call site), this maps
    pack id -> calendar_type (AD/BS) now, not the old fixed pack type."""
    pack_ids = {p for p in pack_ids if p}
    if not pack_ids:
        return {}
    rows = await db.execute(
        select(ClosePack.id, ClosePack.calendar_type).where(
            ClosePack.tenant_id == tenant_id, ClosePack.id.in_(pack_ids)
        )
    )
    return {pid: ptype for pid, ptype in rows.all()}


async def _build_period_map(
    db: AsyncSession, tenant_id: str, period_ids: set[str]
) -> dict[str, Period]:
    period_ids = {p for p in period_ids if p}
    if not period_ids:
        return {}
    rows = await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.id.in_(period_ids))
    )
    return {p.id: p for p in rows.scalars().all()}


async def _build_entity_map(
    db: AsyncSession, tenant_id: str, entity_ids: set[str]
) -> dict[str, str]:
    entity_ids = {e for e in entity_ids if e}
    if not entity_ids:
        return {}
    rows = await db.execute(
        select(Entity.id, Entity.name).where(
            Entity.tenant_id == tenant_id, Entity.id.in_(entity_ids)
        )
    )
    return {eid: name for eid, name in rows.all()}


@router.get("", response_model=list[PlanOut])
async def list_plans(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
    entity_id: str | None = None,
):
    filters = [Plan.tenant_id == me.tenant_id]
    if period_id:
        filters.append(Plan.period_id == period_id)
    if entity_id:
        filters.append(Plan.entity_id == entity_id)

    rows = await db.execute(
        select(Plan).where(*filters).order_by(Plan.created_at.desc())
    )
    plans = list(rows.scalars().all())

    # Visibility rules:
    #   * SUPER_ADMIN / PLATFORM_OWNER — see everything in tenant scope.
    #   * TEAM_LEAD — sees EVERY plan in the tenant (read access to "all
    #     packs whatever admin generates"), same as an admin's browse
    #     view. `can_edit` on each row (below) is what actually gates
    #     structural authority — only `Plan.owner_id == me.id` grants it;
    #     everything else here is view-only.
    #   * TEAM_MEMBER — see plans whose entity is in their accessible scope
    #     (individual task visibility is enforced at the task router).
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER, Role.TEAM_LEAD):
        accessible = await get_accessible_entity_ids(db, me)
        plans = [p for p in plans if p.entity_id in accessible]

    plan_ids = [p.id for p in plans]
    exclude_custom = me.role in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER)
    aggregates = await _aggregate_for_plans(db, me.tenant_id, plan_ids, exclude_custom=exclude_custom)
    pack_types = await _build_pack_type_map(
        db, me.tenant_id, {p.source_pack_id for p in plans}
    )
    period_map = await _build_period_map(db, me.tenant_id, {p.period_id for p in plans})
    entity_map = await _build_entity_map(db, me.tenant_id, {p.entity_id for p in plans})
    owner_map = await _build_user_mini_map(
        db, me.tenant_id, {p.owner_id for p in plans if p.owner_id}
    )

    out: list[PlanOut] = []
    for p in plans:
        total, done, pending_review = aggregates.get(p.id, (0, 0, 0))
        progress = round((done / total) * 100, 2) if total else 0.0
        per = period_map.get(p.period_id)
        out.append(
            PlanOut(
                id=p.id,
                tenant_id=p.tenant_id,
                period_id=p.period_id,
                entity_id=p.entity_id,
                owner_id=p.owner_id,
                source_pack_id=p.source_pack_id,
                source_pack_version=p.source_pack_version,
                title=p.title,
                description=p.description,
                status=p.status,
                due_date=p.due_date,
                created_at=p.created_at,
                task_count=total,
                completed_count=done,
                pending_review_count=pending_review,
                progress=progress,
                calendar_type=pack_types.get(p.source_pack_id) if p.source_pack_id else None,
                period_label=per.label if per else None,
                period_start_date=per.start_date if per else None,
                period_end_date=per.end_date if per else None,
                entity_name=entity_map.get(p.entity_id),
                owner=owner_map.get(p.owner_id) if p.owner_id else None,
                can_edit=await can_lead_edit_plan(db, me, p),
                tasks=[],
            )
        )
    return out


@router.get("/preview", response_model=list[PlanPreviewOut])
async def preview_plans(
    period_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Read-only preview of what `generate_plans` WOULD materialize for
    `period_id`, computed live from each Close Pack's CURRENT template
    tree — no Plan/Task/SubTask/ClosePackVersion row is created. Lets the
    Team Lead (or Admin) dashboard's period picker show a future period's
    expected work — with calculated due dates, including "Upcoming, no
    occurrence this period" recurring tasks — before that period is
    actually the one being generated.

    Returns [] once real plans already exist for `period_id` (that period
    has already been generated — the caller should use GET /plans instead)
    or once there is nothing to preview at all.

    Scope mirrors carry_forward_service._regenerate_plans_for_next_period:
    every (pack, entity) pair the tenant has EVER generated a plan for,
    from a still-active pack, with the most-recently-known owner carried
    forward. On top of the pack templates it previews the tenant's custom
    recurring tasks (a Lead's "Add Task" with a repeat — `is_custom` rows)
    that the period close would materialise into this period, so a task that
    carries on past its own period shows up in the upcoming ones too; they sit
    in the card of the pack/entity plan they were added to (an "Ad-hoc tasks"
    card when that plan has no pack). Visibility mirrors list_plans: a Team
    Lead sees pairs they own (or that are unowned); Admin/Platform Owner see
    every pair; a Team Member gets nothing (there's no "my future work"
    concept here).
    """
    period = (
        await db.execute(
            select(Period).where(Period.id == period_id, Period.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not period:
        raise HTTPException(404, "Period not found")

    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER, Role.TEAM_LEAD):
        return []

    already_generated = (
        await db.execute(
            select(Plan.id).where(Plan.tenant_id == me.tenant_id, Plan.period_id == period_id).limit(1)
        )
    ).scalar_one_or_none()
    if already_generated:
        return []

    rows = await db.execute(
        select(Plan.source_pack_id, Plan.entity_id, Plan.owner_id, Plan.created_at)
        .join(ClosePack, ClosePack.id == Plan.source_pack_id)
        .where(
            Plan.tenant_id == me.tenant_id,
            Plan.source_pack_id.is_not(None),
            ClosePack.active.is_(True),
        )
        .order_by(Plan.created_at)
    )
    # latest owner wins per (pack, entity) — same rule as the real
    # next-period regeneration.
    owner_by_pair: dict[tuple[str, str], Optional[str]] = {}
    for pack_id, entity_id, owner_id, _created in rows.all():
        owner_by_pair[(pack_id, entity_id)] = owner_id
    pairs = list(owner_by_pair.items())

    # The Lead's / Admin's own recurring tasks ("Add Task" with a repeat) have
    # no template, so the pack loop below can't see them — read them back from
    # their stored rule, the same way the period close will materialise them.
    # Each carries an owner: a pack plan's is the pair's latest owner (so it
    # follows the template card it joins), otherwise its own plan's.
    custom = [
        (g, owner_by_pair.get((g.pack_id, g.entity_id), g.owner_id))
        for g in await preview_custom_recurring_for_period(
            db, tenant_id=me.tenant_id, period=period
        )
    ]

    if me.role == Role.TEAM_LEAD:
        pairs = [((p, e), o) for (p, e), o in pairs if o in (None, me.id)]
        custom = [(g, o) for g, o in custom if o in (None, me.id)]
    if not pairs and not custom:
        return []

    pack_ids = {p for (p, _e), _o in pairs} | {g.pack_id for g, _o in custom if g.pack_id}
    entity_ids = {e for (_p, e), _o in pairs} | {g.entity_id for g, _o in custom}
    owner_ids = {o for _pe, o in pairs if o} | {o for _g, o in custom if o}
    packs = {
        p.id: p for p in (
            await db.execute(select(ClosePack).where(ClosePack.id.in_(pack_ids)))
        ).scalars().all()
    }
    entities = {
        e.id: e for e in (
            await db.execute(select(Entity).where(Entity.id.in_(entity_ids)))
        ).scalars().all()
    }
    owners = await _build_user_mini_map(db, me.tenant_id, owner_ids)

    # (pack id or None, entity id) -> card. Template cards first, in order;
    # a Lead's recurring task joins the card of its own plan, or gets an
    # "Ad-hoc tasks" card when it sits in a plan with no Close Pack.
    cards: dict[tuple[Optional[str], str], dict] = {}
    for (pack_id, entity_id), owner_id in pairs:
        pack = packs.get(pack_id)
        entity = entities.get(entity_id)
        if not pack or not entity:
            continue
        templates = await load_task_templates(db, me.tenant_id, pack_id)
        payload = build_snapshot_payload(pack, templates)
        cards[(pack.id, entity.id)] = dict(
            pack_id=pack.id,
            pack_title=pack.name,
            entity_id=entity.id,
            entity_name=entity.name,
            owner=owners.get(owner_id) if owner_id else None,
            period_id=period.id,
            period_label=period.label,
            calendar_type=pack.calendar_type,
            tasks=preview_tasks_for_period(payload["task_templates"], period, pack.calendar_type),
        )
    for group, owner_id in custom:
        entity = entities.get(group.entity_id)
        if not entity:
            continue
        pack = packs.get(group.pack_id) if group.pack_id else None
        card = cards.setdefault(
            (pack.id if pack else None, entity.id),
            dict(
                pack_id=pack.id if pack else None,
                pack_title=pack.name if pack else "Ad-hoc tasks",
                entity_id=entity.id,
                entity_name=entity.name,
                owner=owners.get(owner_id) if owner_id else None,
                period_id=period.id,
                period_label=period.label,
                calendar_type=pack.calendar_type if pack else None,
                tasks=[],
            ),
        )
        card["tasks"] = [*card["tasks"], *group.tasks]
    return [PlanPreviewOut(**card) for card in cards.values()]


async def _member_has_assignment_in_plan(
    db: AsyncSession, tenant_id: str, plan_id: str, user_id: str
) -> bool:
    """True if this Team Member has a Task, whole-entity assignee row, or
    SubTask assigned to them anywhere in this plan — regardless of
    whether the plan's entity is in their normal entity-scope access.
    A task can be handed to a specific person outside their usual scope
    (e.g. covering for someone on another entity); they still need to be
    able to open that one pack and submit their own work in it."""
    entity_task_rows = await db.execute(
        select(TaskEntityAssignee.task_id).where(
            TaskEntityAssignee.tenant_id == tenant_id, TaskEntityAssignee.user_id == user_id
        )
    )
    my_entity_task_ids = {r[0] for r in entity_task_rows.all()}
    task_conditions = [Task.assigned_to_id == user_id, Task.member_assigned_to_id == user_id]
    if my_entity_task_ids:
        task_conditions.append(Task.id.in_(my_entity_task_ids))
    has_task = (
        await db.execute(
            select(Task.id)
            .where(Task.tenant_id == tenant_id, Task.plan_id == plan_id, or_(*task_conditions))
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_task:
        return True
    my_shared_sub_ids = select(SubTaskEntityAssignee.subtask_id).where(
        SubTaskEntityAssignee.tenant_id == tenant_id,
        SubTaskEntityAssignee.user_id == user_id,
    )
    has_sub = (
        await db.execute(
            select(SubTask.id)
            .join(Task, SubTask.task_id == Task.id)
            .where(
                SubTask.tenant_id == tenant_id,
                Task.plan_id == plan_id,
                or_(
                    SubTask.assigned_to_id == user_id,
                    SubTask.id.in_(my_shared_sub_ids),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return has_sub is not None


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    p = (
        await db.execute(
            select(Plan)
            .where(Plan.id == plan_id, Plan.tenant_id == me.tenant_id)
            .options(
                selectinload(Plan.tasks).selectinload(Task.entity_assignees),
                selectinload(Plan.tasks)
                .selectinload(Task.sub_tasks)
                .selectinload(SubTask.entity_assignees),
            )
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Not found")
    # Visibility: Admin/Platform Owner and Team Lead both see any plan in
    # the tenant — "can see all packs whatever admin generates." Whether
    # they can EDIT it is a separate question, answered by `can_edit`
    # below (Lead: only when `owner_id == me.id`). Team Member keeps the
    # narrower accessible-entity-scope visibility — OR, regardless of
    # entity scope, having an actual Task/SubTask assigned to them
    # somewhere in this specific plan (e.g. covering a task on an entity
    # outside their normal scope must still let them open that one pack
    # and submit their own work in it).
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER, Role.TEAM_LEAD):
        accessible = await get_accessible_entity_ids(db, me)
        if p.entity_id not in accessible and not await _member_has_assignment_in_plan(
            db, me.tenant_id, p.id, me.id
        ):
            raise HTTPException(403, "Plan not in your scope")

    # Sort tasks/sub_tasks by deadline: today first, then upcoming, missed last
    today = nepal_today()
    p.tasks.sort(key=lambda t: _task_order(t, today))
    for t in p.tasks:
        t.sub_tasks.sort(key=lambda s: _subtask_order(s, today))

    user_ids = _collect_user_ids([p])
    users = await _build_user_mini_map(db, me.tenant_id, user_ids)

    # Super Admin / Platform Owner see only what the Close Pack template
    # actually generated — a Team Lead's own ad-hoc additions (is_custom)
    # stay theirs to manage and don't show or count here. Filtering the
    # list comprehension below (not reassigning p.tasks itself) — Plan.tasks
    # cascades "all,delete-orphan", so mutating the relationship collection
    # would delete the excluded tasks outright.
    exclude_custom = me.role in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER)

    aggregates = await _aggregate_for_plans(db, me.tenant_id, [p.id], exclude_custom=exclude_custom)
    total, done, pending_review = aggregates.get(p.id, (0, 0, 0))
    progress = round((done / total) * 100, 2) if total else 0.0

    pack_types = await _build_pack_type_map(
        db, me.tenant_id, {p.source_pack_id} if p.source_pack_id else set()
    )
    period_map = await _build_period_map(db, me.tenant_id, {p.period_id})
    entity_map = await _build_entity_map(db, me.tenant_id, {p.entity_id})
    per = period_map.get(p.period_id)

    return PlanOut(
        id=p.id,
        tenant_id=p.tenant_id,
        period_id=p.period_id,
        entity_id=p.entity_id,
        owner_id=p.owner_id,
        source_pack_id=p.source_pack_id,
        source_pack_version=p.source_pack_version,
        title=p.title,
        description=p.description,
        status=p.status,
        due_date=p.due_date,
        created_at=p.created_at,
        task_count=total,
        completed_count=done,
        pending_review_count=pending_review,
        progress=progress,
        calendar_type=pack_types.get(p.source_pack_id) if p.source_pack_id else None,
        period_label=per.label if per else None,
        period_start_date=per.start_date if per else None,
        period_end_date=per.end_date if per else None,
        entity_name=entity_map.get(p.entity_id),
        owner=users.get(p.owner_id) if p.owner_id else None,
        can_edit=await can_lead_edit_plan(db, me, p),
        tasks=[_serialize_task(t, users) for t in p.tasks if not (exclude_custom and t.is_custom)],
    )


@router.post(
    "/generate",
    response_model=PlanGenerateOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def generate(
    payload: PlanGenerateIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    try:
        plans = await generate_plans(
            db,
            tenant_id=me.tenant_id,
            pack_id=payload.close_pack_id,
            period_id=payload.period_id,
            entity_ids=payload.entity_ids,
            user_id=me.id,
            owner_id=payload.owner_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return PlanGenerateOut(
        plans_created=len(plans),
        plan_ids=[p.id for p in plans],
    )


@router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: str,
    payload: PlanUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Structural edit of a Plan envelope (title / description / due_date).

    Permission: admin OR the explicit Plan owner (`Plan.owner_id`) — a
    Team Lead viewing a plan not assigned to them is read-only, full
    stop. This endpoint NEVER touches the source close pack template.
    """
    p = (
        await db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Not found")

    await ensure_lead_edit_plan(db, me, p)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update")

    for field, value in changes.items():
        setattr(p, field, value)

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PLAN_UPDATED",
        entity_type="Plan",
        entity_id=p.id,
        meta={"fields": sorted(changes.keys())},
    )
    await db.commit()
    await db.refresh(p)
    # Delegate enrichment to get_plan for a single source of truth
    return await get_plan(plan_id=p.id, me=me, db=db)


@router.patch("/{plan_id}/owner", response_model=PlanOut)
async def assign_plan_owner(
    plan_id: str,
    payload: PlanOwnerAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin-only: hand a plan to a Team Lead (or reassign / clear).

    Passing `owner_id=null` clears the owner — the plan reverts to admin-
    only structural authority until reassigned.
    """
    ensure_can_assign_plan_owner(me)

    p = (
        await db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Not found")

    new_owner_id = payload.owner_id
    if new_owner_id:
        owner_row = await db.execute(
            select(User).where(User.id == new_owner_id, User.tenant_id == me.tenant_id)
        )
        owner_user = owner_row.scalar_one_or_none()
        if not owner_user:
            raise HTTPException(400, "Proposed owner not found in tenant")
        if owner_user.role not in (Role.TEAM_LEAD, Role.SUPER_ADMIN):
            raise HTTPException(
                400, "Plan owner must be a Team Lead or Super Admin"
            )

    previous = p.owner_id
    p.owner_id = new_owner_id

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PLAN_OWNER_ASSIGNED",
        entity_type="Plan",
        entity_id=p.id,
        meta={"previous_owner_id": previous, "new_owner_id": new_owner_id},
    )
    await db.commit()
    await db.refresh(p)
    return await get_plan(plan_id=p.id, me=me, db=db)


@router.delete(
    "/{plan_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def delete_plan(
    plan_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    p = (
        await db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Not found")
    await db.delete(p)
    await db.commit()
