"""Team Dashboard API (GET/POST/PATCH /api/team/*) — a role-aware,
spec-shaped surface over the existing Task/SubTask/Plan/Attachment
machinery for Team Lead and Team Member consumers.

Every endpoint here delegates to (or directly reuses) the already-
authorized logic in routers/tasks.py, routers/subtasks.py,
routers/plans.py, and routers/dashboards.py rather than re-implementing
access control — this file's job is routing/shaping a specific API
contract, not a second set of authorization rules that could drift out
of sync with the originals.

`{task_id}` in every item-level path below accepts EITHER a Task id or
a SubTask id — a Team Member's flat task list mixes leaf tasks and
sub-tasks (a task with sub-tasks is never itself a work item), and this
lets any row's id be used directly against these endpoints without the
caller needing to know which table it lives in. `GET /team/tasks`
returns the Task-rooted tree (sub-tasks nested), same as GET /api/tasks.
"""

from __future__ import annotations

from typing import Union

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..calendar import nepal_today
from ..database import get_db
from ..models import (
    Attachment,
    AuditLog,
    Entity,
    Period,
    Plan,
    PlanStatus,
    Role,
    SubTask,
    SubTaskEntityAssignee,
    Task,
    TaskEntityAssignee,
    TaskStatus,
    User,
)
from ..schemas import (
    AttachmentOut,
    PlanOut,
    SubTaskOut,
    SubTaskSubmitIn,
    SubmissionHistoryEntryOut,
    TaskBoardCountsOut,
    TaskDetailOut,
    TaskOut,
    TaskSubmitIn,
    TeamAssignIn,
    TeamDashboardOut,
    TeamStatusIn,
    TeamSubmitIn,
)
from ..auth import require_tenant
from ..rollup import recompute_plan, rollup_from_subtask
from ..services import attachment_storage
from ..services.audit_service import log_action
from ..services.dashboard_service import time_logged_today_minutes
from ..services.optional_tasks import is_skipped, task_is_skipped
from ..services.plan_customization_service import (
    ensure_lead_edit_plan,
    is_admin,
    is_team_lead,
    is_team_member,
)

from .tasks import (
    _build_task_out,
    _can_view_task,
    _load_plan as _load_plan_for_task,
    _load_task,
    _serialize_attachment as _serialize_task_attachment,
    _task_has_subtasks,
    _user_mini_map as _task_user_mini_map,
    get_task_detail,
    list_tasks,
    submit_task_for_review,
)
from .subtasks import (
    _build_out as _build_subtask_out,
    _can_view_subtask,
    _load_plan_for_subtask,
    _load_subtask,
    _serialize_attachment as _serialize_subtask_attachment,
    submit_for_review as submit_subtask_for_review,
    upload_subtask_attachment,
)
from .tasks import upload_task_attachment
from .plans import (
    _aggregate_for_plans,
    _build_entity_map,
    _build_pack_type_map,
    _build_period_map,
    _build_user_mini_map,
    get_plan,
    list_plans,
)
from .dashboards import (
    _bucket_for_task,
    _resolve_current_period,
    _resolve_lead_plans,
    _resolve_open_period,
    task_board,
    team_workload,
)

router = APIRouter(prefix="/api/team", tags=["team"])


# ============================================================
# Work-item resolution — a {task_id} path segment may be a Task or a
# SubTask id
# ============================================================
async def _resolve_work_item(
    db: AsyncSession, tenant_id: str, item_id: str
) -> tuple[str, Task | SubTask]:
    exists_task = (
        await db.execute(
            select(Task.id).where(Task.id == item_id, Task.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if exists_task:
        return "task", await _load_task(db, tenant_id, item_id)
    exists_sub = (
        await db.execute(
            select(SubTask.id).where(
                SubTask.id == item_id, SubTask.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if exists_sub:
        return "subtask", await _load_subtask(db, tenant_id, item_id)
    raise HTTPException(404, "Task not found")


# ============================================================
# Dashboard
# ============================================================
@router.get("/dashboard", response_model=TeamDashboardOut)
async def team_dashboard(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
):
    """Role-aware team dashboard stats — Lead/Admin get their
    pack/task counts plus a per-member workload breakdown (via the
    existing team-workload/task-board logic); a Team Member gets the
    same shape scoped to only their own assigned work, with an empty
    `team_workload`."""
    # Always the caller's own total for *today* — independent of period
    # scoping/resolution below, so it's computed once up front and carried
    # into every return path (including the early "no period" ones).
    logged_today = await time_logged_today_minutes(db, me.tenant_id, me.id)

    if is_admin(me) or is_team_lead(me):
        period = await _resolve_open_period(db, me.tenant_id, period_id)
        if not period:
            return TeamDashboardOut(
                role=me.role.value, time_logged_today_minutes=logged_today
            )
        my_plans = await _resolve_lead_plans(db, me, period=period)
        board = await task_board(me=me, db=db, period_id=period.id)
        workload = await team_workload(me=me, db=db, period_id=period.id)
        return TeamDashboardOut(
            role=me.role.value,
            period_id=period.id,
            period_label=period.label,
            pack_count=len(my_plans),
            task_count=board.total_count,
            counts=board.counts,
            team_workload=workload.members,
            time_logged_today_minutes=logged_today,
        )

    # Team Member — own work only.
    period = await _resolve_current_period(db, me.tenant_id, period_id)
    if not period:
        return TeamDashboardOut(
            role=me.role.value, time_logged_today_minutes=logged_today
        )
    plan_rows = await db.execute(
        select(Plan.id).where(Plan.tenant_id == me.tenant_id, Plan.period_id == period.id)
    )
    plan_ids = [pid for (pid,) in plan_rows.all()]
    if not plan_ids:
        return TeamDashboardOut(
            role=me.role.value,
            period_id=period.id,
            period_label=period.label,
            time_logged_today_minutes=logged_today,
        )

    entity_task_rows = await db.execute(
        select(TaskEntityAssignee.task_id).where(
            TaskEntityAssignee.tenant_id == me.tenant_id,
            TaskEntityAssignee.user_id == me.id,
        )
    )
    my_entity_task_ids = {r[0] for r in entity_task_rows.all()}
    task_conditions = [Task.assigned_to_id == me.id, Task.member_assigned_to_id == me.id]
    if my_entity_task_ids:
        task_conditions.append(Task.id.in_(my_entity_task_ids))

    task_rows = await db.execute(
        select(Task)
        .where(Task.tenant_id == me.tenant_id, Task.plan_id.in_(plan_ids), or_(*task_conditions))
        .options(selectinload(Task.sub_tasks))
    )
    my_tasks = list(task_rows.scalars().all())

    my_shared_sub_ids = select(SubTaskEntityAssignee.subtask_id).where(
        SubTaskEntityAssignee.tenant_id == me.tenant_id,
        SubTaskEntityAssignee.user_id == me.id,
    )
    sub_rows = await db.execute(
        select(SubTask, Task.plan_id, Task.is_required)
        .join(Task, SubTask.task_id == Task.id)
        .where(
            SubTask.tenant_id == me.tenant_id,
            Task.plan_id.in_(plan_ids),
            or_(
                SubTask.assigned_to_id == me.id,
                SubTask.id.in_(my_shared_sub_ids),
            ),
        )
    )
    today = nepal_today()
    my_subs: list[SubTask] = []
    sub_plan_ids: set[str] = set()
    for s, pid, parent_required in sub_rows.all():
        sub_plan_ids.add(pid)
        # A sub-task inherits its parent's optional flag; skipped ones
        # (optional, past due, not done) are not counted.
        if not is_skipped(
            is_required=parent_required, status=s.status,
            due_date=s.due_date, today=today,
        ):
            my_subs.append(s)

    pack_ids = {t.plan_id for t in my_tasks} | sub_plan_ids
    # Leaf work items only — a task WITH sub-tasks contributes nothing
    # itself, matching team_workload's dedup rule. Skipped work (optional,
    # past due, not done) is never counted in the totals.
    leaf_items = [
        t for t in my_tasks if not t.sub_tasks and not task_is_skipped(t, today)
    ] + my_subs

    counts = TaskBoardCountsOut(all=len(leaf_items))
    for item in leaf_items:
        bucket = _bucket_for_task(item, today)
        setattr(counts, bucket, getattr(counts, bucket) + 1)

    return TeamDashboardOut(
        role=me.role.value,
        period_id=period.id,
        period_label=period.label,
        pack_count=len(pack_ids),
        task_count=len(leaf_items),
        counts=counts,
        team_workload=[],
        time_logged_today_minutes=logged_today,
    )


# ============================================================
# Packs
# ============================================================
@router.get("/packs", response_model=list[PlanOut])
async def team_packs(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
    entity_id: str | None = None,
):
    """Lead/Admin: every pack they can manage/view (delegates to
    GET /api/plans's existing tenant-wide read for that role). Team
    Member: only packs where they have an assigned task or sub-task —
    stricter than GET /api/plans's entity-scope visibility, per spec."""
    if is_admin(me) or is_team_lead(me):
        return await list_plans(me=me, db=db, period_id=period_id, entity_id=entity_id)

    task_conditions = [Task.assigned_to_id == me.id, Task.member_assigned_to_id == me.id]
    entity_task_rows = await db.execute(
        select(TaskEntityAssignee.task_id).where(
            TaskEntityAssignee.tenant_id == me.tenant_id,
            TaskEntityAssignee.user_id == me.id,
        )
    )
    my_entity_task_ids = {r[0] for r in entity_task_rows.all()}
    if my_entity_task_ids:
        task_conditions.append(Task.id.in_(my_entity_task_ids))

    task_plan_rows = await db.execute(
        select(Task.plan_id).where(Task.tenant_id == me.tenant_id, or_(*task_conditions))
    )
    my_shared_sub_ids = select(SubTaskEntityAssignee.subtask_id).where(
        SubTaskEntityAssignee.tenant_id == me.tenant_id,
        SubTaskEntityAssignee.user_id == me.id,
    )
    sub_plan_rows = await db.execute(
        select(Task.plan_id)
        .join(SubTask, SubTask.task_id == Task.id)
        .where(
            SubTask.tenant_id == me.tenant_id,
            or_(
                SubTask.assigned_to_id == me.id,
                SubTask.id.in_(my_shared_sub_ids),
            ),
        )
    )
    my_plan_ids = {pid for (pid,) in task_plan_rows.all()} | {
        pid for (pid,) in sub_plan_rows.all()
    }
    if not my_plan_ids:
        return []

    filters = [Plan.tenant_id == me.tenant_id, Plan.id.in_(my_plan_ids)]
    if period_id:
        filters.append(Plan.period_id == period_id)
    if entity_id:
        filters.append(Plan.entity_id == entity_id)
    rows = await db.execute(select(Plan).where(*filters).order_by(Plan.created_at.desc()))
    plans = list(rows.scalars().all())

    aggregates = await _aggregate_for_plans(db, me.tenant_id, [p.id for p in plans])
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
                type=pack_types.get(p.source_pack_id) if p.source_pack_id else None,
                period_label=per.label if per else None,
                period_start_date=per.start_date if per else None,
                entity_name=entity_map.get(p.entity_id),
                owner=owner_map.get(p.owner_id) if p.owner_id else None,
                can_edit=False,  # a Team Member never has structural edit rights
                tasks=[],
            )
        )
    return out


@router.get("/packs/{pack_id}", response_model=PlanOut)
async def team_pack_detail(
    pack_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    if is_admin(me) or is_team_lead(me):
        return await get_plan(pack_id, me, db)

    entity_task_rows = await db.execute(
        select(TaskEntityAssignee.task_id).where(
            TaskEntityAssignee.tenant_id == me.tenant_id,
            TaskEntityAssignee.user_id == me.id,
        )
    )
    my_entity_task_ids = {r[0] for r in entity_task_rows.all()}
    task_conditions = [
        Task.assigned_to_id == me.id,
        Task.member_assigned_to_id == me.id,
    ]
    if my_entity_task_ids:
        task_conditions.append(Task.id.in_(my_entity_task_ids))
    has_task = (
        await db.execute(
            select(Task.id)
            .where(Task.tenant_id == me.tenant_id, Task.plan_id == pack_id, or_(*task_conditions))
            .limit(1)
        )
    ).scalar_one_or_none()
    has_sub = None
    if not has_task:
        my_shared_sub_ids = select(SubTaskEntityAssignee.subtask_id).where(
            SubTaskEntityAssignee.tenant_id == me.tenant_id,
            SubTaskEntityAssignee.user_id == me.id,
        )
        has_sub = (
            await db.execute(
                select(SubTask.id)
                .join(Task, SubTask.task_id == Task.id)
                .where(
                    SubTask.tenant_id == me.tenant_id,
                    Task.plan_id == pack_id,
                    or_(
                        SubTask.assigned_to_id == me.id,
                        SubTask.id.in_(my_shared_sub_ids),
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    if not has_task and not has_sub:
        raise HTTPException(403, "You have no assigned task in this pack")
    return await get_plan(pack_id, me, db)


# ============================================================
# Tasks
# ============================================================
@router.get("/tasks", response_model=list[TaskOut])
async def team_tasks(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    plan_id: str | None = None,
    assigned_to_id: str | None = None,
    status: TaskStatus | None = None,
    tag: str | None = None,
):
    """Delegates to GET /api/tasks for the base tiered visibility — a
    Lead sees every task in packs they can manage, a Team Member only
    tasks assigned to them. That direct-assignment check alone misses
    one case though: a task with sub-tasks never itself carries a
    member assignment, so a member whose only stake in a task is via
    one of its sub-tasks wouldn't otherwise see the parent task at all.
    Fold those in too, so nothing assigned to the member is missing
    from their list."""
    base = await list_tasks(
        me=me, db=db, plan_id=plan_id, assigned_to_id=assigned_to_id, status=status, tag=tag
    )
    if not is_team_member(me):
        return base

    seen_ids = {t.id for t in base}
    my_shared_sub_ids = select(SubTaskEntityAssignee.subtask_id).where(
        SubTaskEntityAssignee.tenant_id == me.tenant_id,
        SubTaskEntityAssignee.user_id == me.id,
    )
    filters = [
        Task.tenant_id == me.tenant_id,
        or_(
            SubTask.assigned_to_id == me.id,
            SubTask.id.in_(my_shared_sub_ids),
        ),
    ]
    if plan_id:
        filters.append(Task.plan_id == plan_id)
    extra_rows = await db.execute(
        select(Task.id).join(SubTask, SubTask.task_id == Task.id).where(*filters).distinct()
    )
    extra_ids = [tid for (tid,) in extra_rows.all() if tid not in seen_ids]
    extra = [
        await _build_task_out(db, me.tenant_id, await _load_task(db, me.tenant_id, tid))
        for tid in extra_ids
    ]
    return base + extra


@router.get("/tasks/{task_id}", response_model=Union[TaskDetailOut, SubTaskOut])
async def team_task_detail(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    kind, item = await _resolve_work_item(db, me.tenant_id, task_id)
    if kind == "task":
        return await get_task_detail(task_id, me, db)

    s = item
    if not _can_view_subtask(me, s):
        raise HTTPException(403, "Not allowed")
    entity = (
        await db.execute(
            select(Entity).where(Entity.id == s.entity_id, Entity.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    out = await _build_subtask_out(db, s)
    out.entity_name = entity.name if entity else None
    att_rows = await db.execute(
        select(Attachment)
        .where(Attachment.tenant_id == me.tenant_id, Attachment.subtask_id == s.id)
        .order_by(Attachment.created_at)
    )
    atts = list(att_rows.scalars().all())
    att_users = await _task_user_mini_map(db, me.tenant_id, {a.uploaded_by_id for a in atts})
    out.attachments = [_serialize_subtask_attachment(a, att_users) for a in atts]
    return out


# ============================================================
# Assignment
# ============================================================
async def _assign_work_item(
    task_id: str, payload: TeamAssignIn, me: User, db: AsyncSession
) -> TaskOut | SubTaskOut:
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may assign tasks")

    member = (
        await db.execute(
            select(User).where(User.id == payload.assignee_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not member or member.role != Role.TEAM_MEMBER:
        raise HTTPException(400, "assignee_id must be a Team Member in this tenant")

    kind, item = await _resolve_work_item(db, me.tenant_id, task_id)

    if kind == "subtask":
        s = item
        plan = await _load_plan_for_subtask(db, me.tenant_id, s)
        if plan.status == PlanStatus.CLOSED:
            raise HTTPException(400, "Plan is closed")
        await ensure_lead_edit_plan(db, me, plan)
        s.assigned_to_id = payload.assignee_id
        if s.status in (TaskStatus.NOT_STARTED, TaskStatus.REJECTED):
            s.status = TaskStatus.ASSIGNED
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TEAM_TASK_ASSIGNED",
            entity_type="SubTask",
            entity_id=s.id,
            meta={"assignee_id": payload.assignee_id},
        )
        await rollup_from_subtask(db, s)
        await db.commit()
        s = await _load_subtask(db, me.tenant_id, s.id)
        return await _build_subtask_out(db, s)

    t = item
    plan = await _load_plan_for_task(db, me.tenant_id, t.plan_id)
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    await ensure_lead_edit_plan(db, me, plan)
    if await _task_has_subtasks(db, t.id):
        raise HTTPException(
            400,
            "This task has sub-tasks; assign the specific sub-task instead "
            "(use its id in place of the task id).",
        )
    t.member_assigned_to_id = payload.assignee_id
    if t.status == TaskStatus.NOT_STARTED:
        t.status = TaskStatus.ASSIGNED
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TEAM_TASK_ASSIGNED",
        entity_type="Task",
        entity_id=t.id,
        meta={"assignee_id": payload.assignee_id},
    )
    await recompute_plan(db, plan)
    await db.commit()
    t = await _load_task(db, me.tenant_id, t.id)
    return await _build_task_out(db, me.tenant_id, t)


@router.post("/tasks/{task_id}/assign", response_model=Union[TaskOut, SubTaskOut])
async def assign_work_item(
    task_id: str,
    payload: TeamAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    return await _assign_work_item(task_id, payload, me, db)


@router.patch("/tasks/{task_id}/assignment", response_model=Union[TaskOut, SubTaskOut])
async def reassign_work_item(
    task_id: str,
    payload: TeamAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Same underlying action as POST .../assign — kept as a separate
    route to match the spec (initial assignment vs. reassignment read
    the same either way: set/replace the assignee)."""
    return await _assign_work_item(task_id, payload, me, db)


# ============================================================
# Status
# ============================================================
_MEMBER_STATUS_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.NOT_STARTED: {TaskStatus.IN_PROGRESS},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS},
    TaskStatus.REJECTED: {TaskStatus.IN_PROGRESS},
    # Withdraw a submission that hasn't been reviewed yet — the assignee
    # (or a Lead/Admin) pulls it back to IN_PROGRESS to keep working on it,
    # then re-submits via POST .../submit. Completion notes/time are left
    # intact as a starting point for the resubmission.
    TaskStatus.PENDING_REVIEW: {TaskStatus.IN_PROGRESS},
}


@router.patch("/tasks/{task_id}/status", response_model=Union[TaskOut, SubTaskOut])
async def update_work_item_status(
    task_id: str,
    payload: TeamStatusIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """"Start work", or withdraw an un-reviewed submission — see
    TeamStatusIn. Reaching PENDING_REVIEW or COMPLETED must go through
    POST .../submit and a Lead's review, respectively; this endpoint
    deliberately can't skip either."""
    kind, item = await _resolve_work_item(db, me.tenant_id, task_id)
    is_assignee = (
        item.assigned_to_id == me.id
        if kind == "subtask"
        else me.id in (item.assigned_to_id, item.member_assigned_to_id)
    )
    if not is_assignee and not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only the assignee or a lead/admin may update status")

    # A shared (whole-entity / multi-assign) task tracks each person's part
    # via TaskEntityAssignee rows — it reaches PENDING_REVIEW only once
    # everyone is done, and is pulled back by re-marking a part, not by a
    # blanket status flip here.
    if (
        kind == "task"
        and item.status == TaskStatus.PENDING_REVIEW
        and getattr(item, "assigned_entity_wide", False)
    ):
        raise HTTPException(
            400,
            "This task is shared between several people — it can't be "
            "withdrawn from review here.",
        )

    allowed = _MEMBER_STATUS_TRANSITIONS.get(item.status, set())
    if payload.status not in allowed:
        raise HTTPException(
            400,
            f"Cannot move status from {item.status.value} to "
            f"{payload.status.value} here. Allowed: "
            f"{', '.join(s.value for s in allowed) or '(none — use submit/review instead)'}",
        )
    item.status = payload.status
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TEAM_STATUS_CHANGED",
        entity_type="Task" if kind == "task" else "SubTask",
        entity_id=item.id,
        meta={"to": payload.status.value},
    )

    if kind == "subtask":
        await rollup_from_subtask(db, item)
        await db.commit()
        s = await _load_subtask(db, me.tenant_id, item.id)
        return await _build_subtask_out(db, s)

    plan = await _load_plan_for_task(db, me.tenant_id, item.plan_id)
    await recompute_plan(db, plan)
    await db.commit()
    t = await _load_task(db, me.tenant_id, item.id)
    return await _build_task_out(db, me.tenant_id, t)


# ============================================================
# Submit
# ============================================================
@router.post("/tasks/{task_id}/submit", response_model=Union[TaskOut, SubTaskOut])
async def submit_work_item(
    task_id: str,
    payload: TeamSubmitIn | None = None,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    kind, _item = await _resolve_work_item(db, me.tenant_id, task_id)
    notes = payload.comment if payload else None
    minutes = payload.time_minutes if payload else None
    if kind == "task":
        return await submit_task_for_review(
            task_id,
            TaskSubmitIn(completion_notes=notes, completion_time_minutes=minutes),
            me,
            db,
        )
    return await submit_subtask_for_review(
        task_id,
        SubTaskSubmitIn(completion_notes=notes, completion_time_minutes=minutes),
        me,
        db,
    )


@router.get("/tasks/{task_id}/submissions", response_model=list[SubmissionHistoryEntryOut])
async def work_item_submissions(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Submission/status audit trail for one task or sub-task — a Team
    Member's own history (they can only reach an item they can view,
    which for a member means one assigned to them), or a Lead/Admin
    reviewing what happened on any item in their scope."""
    kind, item = await _resolve_work_item(db, me.tenant_id, task_id)
    if kind == "task":
        if not _can_view_task(me, item):
            raise HTTPException(403, "Not allowed")
        entity_type = "Task"
    else:
        if not _can_view_subtask(me, item):
            raise HTTPException(403, "Not allowed")
        entity_type = "SubTask"

    rows = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == me.tenant_id,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == item.id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    logs = list(rows.scalars().all())
    users = await _task_user_mini_map(db, me.tenant_id, {l.user_id for l in logs if l.user_id})
    return [
        SubmissionHistoryEntryOut(
            id=log.id,
            action=log.action,
            user_id=log.user_id,
            user=users.get(log.user_id) if log.user_id else None,
            meta=log.meta,
            created_at=log.created_at,
        )
        for log in logs
    ]


# ============================================================
# Attachments
# ============================================================
@router.post("/tasks/{task_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_work_item_attachment(
    task_id: str,
    file: UploadFile = File(...),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    kind, _item = await _resolve_work_item(db, me.tenant_id, task_id)
    if kind == "task":
        return await upload_task_attachment(task_id, file, me, db)
    return await upload_subtask_attachment(task_id, file, me, db)


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Fetches one attachment's file content directly by id. Visibility
    is re-derived from the attachment's parent task/sub-task on every
    call (not just a tenant_id match) — the same rule enforced at
    GET /api/tasks/{id}/attachments/..., so changing the id in the URL
    can never reach another team's file."""
    att = (
        await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id, Attachment.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not att:
        raise HTTPException(404, "Attachment not found")

    if att.task_id:
        t = await _load_task(db, me.tenant_id, att.task_id)
        if not _can_view_task(me, t):
            raise HTTPException(403, "Not allowed")
    else:
        s = await _load_subtask(db, me.tenant_id, att.subtask_id)
        if not _can_view_subtask(me, s):
            raise HTTPException(403, "Not allowed")

    try:
        content = await attachment_storage.read(att.storage_path)
    except FileNotFoundError:
        raise HTTPException(404, "Stored file is missing")
    return Response(
        content=content,
        media_type=att.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{att.filename}"'},
    )
