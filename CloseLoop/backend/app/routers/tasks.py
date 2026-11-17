"""Tasks — created via plan generation; assignment + status updates here."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..calendar import nepal_today
from ..calendar.nepali_calendar import due_label as _due_label
from ..database import get_db
from ..models import (
    Attachment,
    Comment,
    DueDateType,
    Entity,
    Period,
    Plan,
    PlanStatus,
    SubTask,
    Task,
    TaskEntityAssignee,
    User,
    UserEntityAssignment,
    Role,
    TaskStatus,
)
from ..schemas import (
    AttachmentOut,
    CommentCreate,
    CommentOut,
    SubTaskOut,
    TaskAssignSeriesIn,
    TaskBulkAssignIn,
    TaskBulkResultOut,
    TaskBulkReviewIn,
    TaskBulkSkipOut,
    TaskCreate,
    TaskDetailOut,
    TaskEntityAssigneeOut,
    TaskEntityDoneIn,
    TaskMultiAssignIn,
    TaskOut,
    TaskReviewIn,
    TaskSeriesAssignOut,
    TaskSubmitIn,
    TaskUpdate,
    UserMini,
    UserOut,
)
from ..auth import require_tenant
from ..rollup import recompute_plan, recompute_task
from ..serialization import (
    subtask_entity_assignee_user_ids,
    subtask_entity_assignees_out,
)
from ..services import attachment_storage
from ..services.access_service import (
    get_accessible_entity_ids,
    get_lead_direct_entity_ids,
)
from ..services.audit_service import log_action
from ..services.due_date_rules import (
    DueDateOutOfRangeError,
    validate_due_date,
    validate_subtask_due_date,
    validate_working_day,
)
from ..services.notification_service import (
    notify_user_task_approved,
    notify_user_task_assigned,
    notify_user_task_rejected,
    notify_user_task_submitted_for_review,
)
from ..services.time_entry_service import record_completion_time_entry
from ..services.plan_customization_service import (
    TASK_MEMBER_ALLOWED_FIELDS,
    ensure_admin_task_assignment_allowed,
    ensure_completion_details,
    ensure_lead_edit_plan,
    ensure_member_update_allowed,
    is_admin,
    is_team_lead,
    is_team_member,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


async def _load_task(db, tenant_id, task_id) -> Task:
    t = (
        await db.execute(
            select(Task)
            .where(Task.id == task_id, Task.tenant_id == tenant_id)
            .options(
                selectinload(Task.entity_assignees),
                selectinload(Task.sub_tasks).selectinload(SubTask.entity_assignees),
            )
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Task not found")
    return t


def _ensure_not_upcoming(t: Task) -> None:
    """A task materialized into a future period is visible but locked — no
    assignment, edits, or work until that period becomes the running one."""
    if t.status == TaskStatus.UPCOMING:
        raise HTTPException(
            409,
            "This task is in an upcoming period — it becomes actionable once "
            "that period is running.",
        )


async def _user_mini_map(
    db: AsyncSession, tenant_id: str, ids: set[str]
) -> dict[str, "UserMini"]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = await db.execute(
        select(User).where(User.id.in_(ids), User.tenant_id == tenant_id)
    )
    return {u.id: UserMini(id=u.id, name=u.name, title=u.title) for u in rows.scalars().all()}


def _serialize_subtask(s: SubTask, users: dict[str, "UserMini"]) -> "SubTaskOut":
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
        updated_at=s.updated_at,
        assigned_to=users.get(s.assigned_to_id) if s.assigned_to_id else None,
        reviewer=users.get(s.reviewer_id) if s.reviewer_id else None,
        submitted_by=users.get(s.submitted_by_id) if s.submitted_by_id else None,
    )


def _serialize_attachment(
    a: Attachment, users: dict[str, "UserMini"]
) -> "AttachmentOut":
    return AttachmentOut(
        id=a.id,
        task_id=a.task_id,
        subtask_id=a.subtask_id,
        uploaded_by_id=a.uploaded_by_id,
        filename=a.filename,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        created_at=a.created_at,
        uploaded_by=users.get(a.uploaded_by_id),
    )


async def _entity_member_ids(db: AsyncSession, tenant_id: str, entity_id: str) -> set[str]:
    """Every TEAM_MEMBER scoped to an entity: primary_entity_id match, or an
    explicit secondary UserEntityAssignment to that entity."""
    rows = await db.execute(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.role == Role.TEAM_MEMBER,
            or_(
                User.primary_entity_id == entity_id,
                User.id.in_(
                    select(UserEntityAssignment.user_id).where(
                        UserEntityAssignment.tenant_id == tenant_id,
                        UserEntityAssignment.entity_id == entity_id,
                    )
                ),
            ),
        )
    )
    return {r[0] for r in rows.all()}


async def _load_plan(db: AsyncSession, tenant_id: str, plan_id: str) -> Plan:
    plan = (
        await db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


async def _load_period(db: AsyncSession, tenant_id: str, period_id: str) -> Period:
    period = (
        await db.execute(
            select(Period).where(Period.id == period_id, Period.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not period:
        raise HTTPException(404, "Period not found")
    return period


async def _task_has_subtasks(db: AsyncSession, task_id: str) -> bool:
    row = await db.execute(select(SubTask.id).where(SubTask.task_id == task_id).limit(1))
    return row.scalar_one_or_none() is not None


def _resolve_task_reviewer(t: Task, plan: Plan) -> str | None:
    """Who should be notified when this task is submitted for review: the
    Team Lead who owns the task, else the plan owner."""
    return t.assigned_to_id or plan.owner_id


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    plan_id: str | None = None,
    assigned_to_id: str | None = None,
    status: TaskStatus | None = None,
    tag: str | None = None,
):
    filters = [Task.tenant_id == me.tenant_id]
    if plan_id:
        filters.append(Task.plan_id == plan_id)
    if assigned_to_id:
        filters.append(Task.assigned_to_id == assigned_to_id)
    if status:
        filters.append(Task.status == status)
    rows = await db.execute(
        select(Task)
        .where(*filters)
        .options(
            selectinload(Task.entity_assignees),
            selectinload(Task.sub_tasks).selectinload(SubTask.entity_assignees),
        )
        .order_by(Task.created_at)
    )
    tasks = list(rows.scalars().all())
    if tag:
        needle = tag.strip().lower()
        tasks = [t for t in tasks if needle in (s.lower() for s in t.tags)]
    if is_team_member(me):
        # Tier-3 visibility: only see tasks explicitly assigned to me,
        # either as the (legacy) direct assignee on an ad-hoc task, as the
        # executing member on a leaf task the Lead handed off via
        # member_assigned_to_id, or as one of the entity-wide assignees on
        # a task the Lead fanned out to the whole entity. Unassigned tasks
        # remain hidden because the lead has not yet handed them off.
        entity_task_rows = await db.execute(
            select(TaskEntityAssignee.task_id).where(
                TaskEntityAssignee.tenant_id == me.tenant_id,
                TaskEntityAssignee.user_id == me.id,
            )
        )
        my_entity_task_ids = {r[0] for r in entity_task_rows.all()}
        tasks = [
            t
            for t in tasks
            if t.assigned_to_id == me.id
            or t.member_assigned_to_id == me.id
            or t.id in my_entity_task_ids
        ]
    elif me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        if is_team_lead(me):
            # Strict lead visibility: primary entity + direct secondary
            # entities, or plans this lead owns, or tasks assigned to them.
            direct_entities = await get_lead_direct_entity_ids(db, me)
            owned_plan_rows = await db.execute(
                select(Plan.id).where(
                    Plan.tenant_id == me.tenant_id, Plan.owner_id == me.id
                )
            )
            owned_plan_ids = {pid for (pid,) in owned_plan_rows.all()}
            tasks = [
                t
                for t in tasks
                if t.entity_id in direct_entities
                or t.plan_id in owned_plan_ids
                or t.assigned_to_id == me.id
            ]
        else:
            accessible = await get_accessible_entity_ids(db, me)
            tasks = [t for t in tasks if t.entity_id in accessible]
    return [await _build_task_out(db, me.tenant_id, t) for t in tasks]


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(
    payload: TaskCreate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a custom task to a plan (admin or plan-owning lead) — the "Add
    Task" modal: name, due date, priority, optional details/tags, and
    either a direct Team Member assignee OR a set of inline sub-tasks
    (each falling back to the task-level assignee/due date/priority
    when it doesn't specify its own). Mutually exclusive, same rule as
    PATCH /{id}: a task with sub-tasks can't also carry a direct member
    assignment.
    """
    if me.role not in (Role.SUPER_ADMIN, Role.TEAM_LEAD):
        raise HTTPException(403, "Only lead/admin may create custom tasks")

    plan = await _load_plan(db, me.tenant_id, payload.plan_id)
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Cannot add tasks to a closed plan")

    await ensure_lead_edit_plan(db, me, plan)

    # Recurring custom task — materialize instances across periods, same
    # rules as a Close Pack template.
    if payload.frequency is not None:
        from ..services.plan_generation_service import materialize_custom_recurring_task

        # A hand-picked group (2+ people) to whole-assign every occurrence
        # to, instead of the single member_assigned_to_id path. Validated
        # the same way assign-multi validates its user_ids.
        multi_ids: set[str] | None = None
        if payload.member_ids:
            multi_ids = set(payload.member_ids)
            if len(multi_ids) < 2:
                raise HTTPException(
                    400, "member_ids needs at least 2 people — use member_assigned_to_id for a single assignee"
                )
            rows = await db.execute(
                select(User).where(User.id.in_(multi_ids), User.tenant_id == me.tenant_id)
            )
            found = {u.id: u for u in rows.scalars().all()}
            for uid in multi_ids:
                u = found.get(uid)
                if not u or (u.role != Role.TEAM_MEMBER and uid != me.id):
                    raise HTTPException(
                        400, f"user_id {uid} must be a Team Member in this tenant"
                    )
            if payload.subtasks:
                raise HTTPException(
                    400, "A task with sub-tasks can't also carry member_ids — assign each sub-task instead"
                )

        first = await materialize_custom_recurring_task(
            db, tenant_id=me.tenant_id, start_plan=plan, payload=payload, member_ids=multi_ids,
        )
        await log_action(
            db, tenant_id=me.tenant_id, user_id=me.id, action="TASK_CREATED",
            entity_type="Task", entity_id=first.id if first else None,
            meta={"custom_recurring": True, "frequency": payload.frequency.value},
        )
        await db.commit()
        t = await _load_task(db, me.tenant_id, first.id)
        return await _build_task_out(db, me.tenant_id, t)

    due_date = payload.due_date or plan.due_date
    period = await _load_period(db, me.tenant_id, plan.period_id)
    try:
        # Only the date the caller actually picked — `due_date` may have
        # fallen back to the plan's, which is the period's last day.
        validate_working_day(payload.due_date, label="Task")
        validate_due_date(
            period_start=period.start_date, period_end=period.end_date,
            due_date=due_date, due_date_type=payload.due_date_type,
            post_period_due_days=payload.post_period_due_days,
        )
    except DueDateOutOfRangeError as e:
        raise HTTPException(400, str(e))
    has_subtasks = bool(payload.subtasks)
    # With sub-tasks present, member_assigned_to_id isn't set on the task
    # itself — it's only the fallback assignee for rows that don't pick
    # their own.
    task_member_id = None if has_subtasks else payload.member_assigned_to_id

    # Validate every distinct Team Member id referenced anywhere in this
    # payload up front — the top-level assignee/fallback, and each
    # subtask row's own override.
    member_ids = {
        uid
        for uid in (
            [payload.member_assigned_to_id]
            + [s.assigned_to_id for s in payload.subtasks]
        )
        if uid
    }
    if member_ids:
        rows = await db.execute(
            select(User).where(User.id.in_(member_ids), User.tenant_id == me.tenant_id)
        )
        found = {u.id: u for u in rows.scalars().all()}
        for uid in member_ids:
            u = found.get(uid)
            # A Team Lead/Admin with structural authority over this plan may
            # also assign work to themselves, not just to Team Members.
            if not u or (u.role != Role.TEAM_MEMBER and uid != me.id):
                raise HTTPException(
                    400, f"assigned_to_id {uid} must be a Team Member in this tenant"
                )

    # A subtask row that doesn't override due_date inherits the parent
    # task's due_date AND its due_date_type/post_period_due_days (same
    # fall-back rule as due_date itself) — otherwise a subtask silently
    # inheriting an AFTER_PERIOD_END parent's due date would get validated
    # against its own default WITHIN_PERIOD window and be rejected.
    sub_schedules: list[tuple[object, object, int]] = []
    for sub in payload.subtasks:
        if sub.due_date is not None:
            sub_due, sub_type, sub_days = sub.due_date, sub.due_date_type, sub.post_period_due_days
        else:
            sub_due, sub_type, sub_days = due_date, payload.due_date_type, payload.post_period_due_days
        try:
            validate_working_day(sub.due_date, label="Sub-task")
            validate_subtask_due_date(
                period_start=period.start_date, period_end=period.end_date,
                due_date=sub_due, due_date_type=sub_type,
                post_period_due_days=sub_days, parent_task_due_date=due_date,
            )
        except DueDateOutOfRangeError as e:
            raise HTTPException(400, str(e))
        sub_schedules.append((sub_due, sub_type, sub_days))

    status = TaskStatus.ASSIGNED if task_member_id else TaskStatus.NOT_STARTED
    task = Task(
        tenant_id=me.tenant_id,
        plan_id=payload.plan_id,
        entity_id=plan.entity_id,
        source_template_id=None,
        is_custom=True,
        assigned_to_id=payload.assigned_to_id,
        member_assigned_to_id=task_member_id,
        title=payload.title,
        description=payload.description,
        due_date=due_date,
        due_time=payload.due_time,
        due_date_type=payload.due_date_type,
        post_period_due_days=payload.post_period_due_days,
        priority=payload.priority,
        tags=payload.tags,
        status=status,
    )
    db.add(task)
    await db.flush()

    for sub, (sub_due, sub_type, sub_days) in zip(payload.subtasks, sub_schedules):
        sub_assignee = sub.assigned_to_id or payload.member_assigned_to_id
        db.add(
            SubTask(
                tenant_id=me.tenant_id,
                task_id=task.id,
                entity_id=task.entity_id,
                source_template_id=None,
                is_custom=True,
                assigned_to_id=sub_assignee,
                title=sub.title,
                description=sub.description,
                due_date=sub_due,
                due_date_type=sub_type,
                post_period_due_days=sub_days,
                due_time=sub.due_time,
                priority=sub.priority or payload.priority,
                tags=sub.tags,
                status=TaskStatus.ASSIGNED if sub_assignee else TaskStatus.NOT_STARTED,
            )
        )
    if payload.subtasks:
        await db.flush()
        await recompute_task(db, task)

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TASK_CREATED",
        entity_type="Task",
        entity_id=task.id,
        meta={"plan_id": plan.id, "is_custom": True, "subtask_count": len(payload.subtasks)},
    )
    if payload.assigned_to_id and payload.assigned_to_id != me.id:
        await notify_user_task_assigned(
            db, me.tenant_id, payload.assigned_to_id, task.id, task.title
        )
    if task_member_id and task_member_id != me.id:
        await notify_user_task_assigned(
            db, me.tenant_id, task_member_id, task.id, task.title
        )
    for sub in payload.subtasks:
        sub_assignee = sub.assigned_to_id or payload.member_assigned_to_id
        if sub_assignee and sub_assignee != me.id and sub_assignee != task_member_id:
            await notify_user_task_assigned(
                db, me.tenant_id, sub_assignee, task.id, sub.title
            )
    await recompute_plan(db, plan)
    await db.commit()
    task = await _load_task(db, me.tenant_id, task.id)
    return await _build_task_out(db, me.tenant_id, task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Delete a CUSTOM task only. Template-sourced tasks are immutable structure."""
    t = await _load_task(db, me.tenant_id, task_id)
    if not t.is_custom:
        raise HTTPException(
            403,
            "Only custom tasks may be deleted; template tasks remain part of the plan",
        )
    if me.role not in (Role.SUPER_ADMIN, Role.TEAM_LEAD):
        raise HTTPException(403, "Not allowed")
    plan = await _load_plan(db, me.tenant_id, t.plan_id)
    await ensure_lead_edit_plan(db, me, plan)
    await db.delete(t)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TASK_DELETED",
        entity_type="Task",
        entity_id=task_id,
        meta={"plan_id": plan.id},
    )
    await db.commit()


def _can_view_task(me: User, t: Task) -> bool:
    """Same visibility rule everywhere a single task is fetched: a Team
    Member only sees tasks they execute; Admin/Lead see any task in the
    tenant (mirrors the tenant-wide plan-read model — editing is what's
    ownership-gated, not viewing). A member executing one of the task's
    sub-tasks (as assignee or reviewer) counts too — that's the normal
    way work reaches a member once a task has sub-tasks, since the task
    itself can no longer carry a direct member assignment at that point."""
    if not is_team_member(me):
        return True
    return (
        t.assigned_to_id == me.id
        or t.member_assigned_to_id == me.id
        or any(a.user_id == me.id for a in t.entity_assignees)
        or any(
            s.assigned_to_id == me.id
            or s.reviewer_id == me.id
            or any(sa.user_id == me.id for sa in (s.entity_assignees or []))
            for s in t.sub_tasks
        )
    )


async def _build_task_out(db: AsyncSession, tenant_id: str, t: Task) -> TaskOut:
    """Properly nested TaskOut — `TaskOut.model_validate(t)` alone can't
    populate `subtasks` (the ORM relationship is named `sub_tasks`), so
    every read path that needs sub-tasks goes through this instead."""
    ids: set[str] = set()
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
    users = await _user_mini_map(db, tenant_id, ids)

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
        updated_at=t.updated_at,
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


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")
    return await _build_task_out(db, me.tenant_id, t)


@router.get("/{task_id}/team", response_model=list[UserOut])
async def task_team(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Team Members eligible to be assigned this leaf task — everyone scoped
    to the task's entity (primary entity or an explicit secondary
    assignment), sorted by name. Drives the "Assign from team" picker in
    the task detail panel, which scales past a handful of chips.

    Falls back to every Team Member in the tenant when nobody is scoped to
    the entity yet, so the Lead can still assign the task.
    """
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may view the team")
    t = await _load_task(db, me.tenant_id, task_id)

    ids = await _entity_member_ids(db, me.tenant_id, t.entity_id)
    if ids:
        rows = await db.execute(
            select(User)
            .where(User.id.in_(ids), User.tenant_id == me.tenant_id)
            .order_by(User.name)
        )
    else:
        rows = await db.execute(
            select(User)
            .where(User.tenant_id == me.tenant_id, User.role == Role.TEAM_MEMBER)
            .order_by(User.name)
        )
    return list(rows.scalars().all())


@router.get("/{task_id}/detail", response_model=TaskDetailOut)
async def get_task_detail(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Everything the task detail side panel needs in one call: the task
    itself, its full sub-task list (each with its own assignee/due
    date/status/priority), entity + period context, progress, and the
    comment thread.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")

    plan = await _load_plan(db, me.tenant_id, t.plan_id)
    period = None
    if plan.period_id:
        period = (
            await db.execute(select(Period).where(Period.id == plan.period_id))
        ).scalar_one_or_none()
    entity = (
        await db.execute(
            select(Entity).where(Entity.id == t.entity_id, Entity.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()

    total_subtasks = len(t.sub_tasks)
    completed_subtasks = sum(1 for st in t.sub_tasks if st.status == TaskStatus.COMPLETED)
    if total_subtasks:
        progress_percentage = round((completed_subtasks / total_subtasks) * 100, 1)
    else:
        progress_percentage = 100.0 if t.status == TaskStatus.COMPLETED else 0.0

    # Days remaining until the TASK's own due date (negative = overdue —
    # deliberately not clamped to 0, the panel needs to tell "12 days
    # left" apart from "3 days overdue").
    days_left = (t.due_date - nepal_today()).days if t.due_date else None

    task_out = await _build_task_out(db, me.tenant_id, t)

    comment_rows = await db.execute(
        select(Comment)
        .where(Comment.tenant_id == me.tenant_id, Comment.task_id == task_id)
        .order_by(Comment.created_at)
    )
    comments = list(comment_rows.scalars().all())
    comment_users = await _user_mini_map(
        db, me.tenant_id, {c.user_id for c in comments}
    )

    # Attachments — the task's own, plus each nested sub-task's own (so
    # the side panel gets everything in this one call).
    subtask_ids = [s.id for s in t.sub_tasks]
    att_rows = await db.execute(
        select(Attachment).where(
            Attachment.tenant_id == me.tenant_id,
            or_(
                Attachment.task_id == task_id,
                Attachment.subtask_id.in_(subtask_ids) if subtask_ids else False,
            ),
        )
        .order_by(Attachment.created_at)
    )
    attachments = list(att_rows.scalars().all())
    att_users = await _user_mini_map(
        db, me.tenant_id, {a.uploaded_by_id for a in attachments}
    )
    task_attachments = [
        _serialize_attachment(a, att_users) for a in attachments if a.task_id == task_id
    ]
    attachments_by_subtask: dict[str, list[AttachmentOut]] = {}
    for a in attachments:
        if a.subtask_id:
            attachments_by_subtask.setdefault(a.subtask_id, []).append(
                _serialize_attachment(a, att_users)
            )
    for sub_out in task_out.subtasks:
        sub_out.attachments = attachments_by_subtask.get(sub_out.id, [])
    # TaskOut carries entity_name too (for the member-dashboard read path);
    # set it here before dumping so it isn't passed twice below.
    task_out.entity_name = entity.name if entity else None

    # How many occurrences of this recurring task sit in the same plan —
    # drives the "assign every occurrence" affordance in the side panel.
    # A task with sub-tasks is skipped by that task-level bulk-assign
    # (assignment for it lives on each sub-task instead), so we also work
    # out, per sub-task, how many sibling occurrences carry the SAME
    # sub-task — matched by sub-task template, or by title for a custom
    # recurring task's sub-tasks (which have no template row) — so the
    # panel can offer the same "assign every occurrence" action there.
    series_count = 1
    series_filter = None
    if t.recurring_group_id:
        series_filter = Task.recurring_group_id == t.recurring_group_id
    elif t.source_template_id:
        series_filter = Task.source_template_id == t.source_template_id
    if series_filter is not None:
        sib_rows = await db.execute(
            select(Task)
            .where(
                Task.tenant_id == me.tenant_id,
                Task.plan_id == t.plan_id,
                Task.entity_id == t.entity_id,
                series_filter,
            )
            .options(selectinload(Task.sub_tasks))
        )
        siblings = list(sib_rows.scalars().all())
        series_count = len(siblings) or 1
        if t.sub_tasks:
            sub_series_counts: dict[str, int] = {}
            for sib in siblings:
                for sub in sib.sub_tasks:
                    key = sub.source_template_id or f"title:{sub.title}"
                    sub_series_counts[key] = sub_series_counts.get(key, 0) + 1
            subout_by_id = {so.id: so for so in task_out.subtasks}
            for sub in t.sub_tasks:
                so = subout_by_id.get(sub.id)
                if so is None:
                    continue
                key = sub.source_template_id or f"title:{sub.title}"
                so.series_count = sub_series_counts.get(key, 1)

    return TaskDetailOut(
        **task_out.model_dump(),
        series_count=series_count,
        period_id=plan.period_id,
        period_label=period.label if period else None,
        period_start_date=period.start_date if period else None,
        period_end_date=period.end_date if period else None,
        days_left=days_left,
        due_label=_due_label(t.due_date),
        completed_subtasks=completed_subtasks,
        total_subtasks=total_subtasks,
        progress_percentage=progress_percentage,
        comments=[
            CommentOut(
                id=c.id,
                task_id=c.task_id,
                user_id=c.user_id,
                body=c.body,
                created_at=c.created_at,
                user=comment_users.get(c.user_id),
            )
            for c in comments
        ],
        attachments=task_attachments,
    )


# ============================================================
# Comments — the task detail panel's discussion thread
# ============================================================
@router.get("/{task_id}/comments", response_model=list[CommentOut])
async def list_comments(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")
    rows = await db.execute(
        select(Comment)
        .where(Comment.tenant_id == me.tenant_id, Comment.task_id == task_id)
        .order_by(Comment.created_at)
    )
    comments = list(rows.scalars().all())
    users = await _user_mini_map(db, me.tenant_id, {c.user_id for c in comments})
    return [
        CommentOut(
            id=c.id,
            task_id=c.task_id,
            user_id=c.user_id,
            body=c.body,
            created_at=c.created_at,
            user=users.get(c.user_id),
        )
        for c in comments
    ]


@router.post("/{task_id}/comments", response_model=CommentOut, status_code=201)
async def create_comment(
    task_id: str,
    payload: CommentCreate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Anyone who can VIEW the task (same rule as GET) can post a
    comment — commenting is a lower bar than structural edit rights,
    it's just discussion."""
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")

    c = Comment(
        tenant_id=me.tenant_id,
        task_id=task_id,
        user_id=me.id,
        body=payload.body,
    )
    db.add(c)
    await db.flush()
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="COMMENT_CREATED",
        entity_type="Comment",
        entity_id=c.id,
        meta={"task_id": task_id},
    )
    await db.commit()
    await db.refresh(c)
    return CommentOut(
        id=c.id,
        task_id=c.task_id,
        user_id=c.user_id,
        body=c.body,
        created_at=c.created_at,
        user=UserMini(id=me.id, name=me.name, title=me.title),
    )


@router.delete("/{task_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    task_id: str,
    comment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """The comment's author, or an admin, may delete it."""
    c = (
        await db.execute(
            select(Comment).where(
                Comment.id == comment_id,
                Comment.task_id == task_id,
                Comment.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Comment not found")
    if c.user_id != me.id and not is_admin(me):
        raise HTTPException(403, "Only the author or an admin may delete this comment")
    await db.delete(c)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="COMMENT_DELETED",
        entity_type="Comment",
        entity_id=comment_id,
        meta={"task_id": task_id},
    )
    await db.commit()


# ============================================================
# Attachments — files on the task itself (see subtasks.py for the
# sub-task-level equivalent). Same visibility bar as comments: anyone
# who can view the task can upload/list/download; only the uploader or
# an admin can delete.
# ============================================================
@router.get("/{task_id}/attachments", response_model=list[AttachmentOut])
async def list_task_attachments(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")
    rows = await db.execute(
        select(Attachment)
        .where(Attachment.tenant_id == me.tenant_id, Attachment.task_id == task_id)
        .order_by(Attachment.created_at)
    )
    atts = list(rows.scalars().all())
    users = await _user_mini_map(db, me.tenant_id, {a.uploaded_by_id for a in atts})
    return [_serialize_attachment(a, users) for a in atts]


@router.post("/{task_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_task_attachment(
    task_id: str,
    file: UploadFile = File(...),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Lead/Admin at assignment time, or the executing Team Member later
    — anyone who can view the task may attach a file to it."""
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")

    try:
        content = await attachment_storage.read_upload_capped(file)
    except ValueError as e:
        raise HTTPException(400, str(e))
    safe_name = attachment_storage.sanitize_filename(file.filename or "file")
    try:
        storage_path = await attachment_storage.save(me.tenant_id, safe_name, content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    att = Attachment(
        tenant_id=me.tenant_id,
        task_id=task_id,
        uploaded_by_id=me.id,
        filename=safe_name,
        content_type=file.content_type,
        size_bytes=len(content),
        storage_path=storage_path,
    )
    db.add(att)
    await db.flush()
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ATTACHMENT_UPLOADED",
        entity_type="Attachment",
        entity_id=att.id,
        meta={"task_id": task_id, "filename": safe_name},
    )
    await db.commit()
    await db.refresh(att)
    return _serialize_attachment(att, {me.id: UserMini(id=me.id, name=me.name, title=me.title)})


@router.get("/{task_id}/attachments/{attachment_id}/download")
async def download_task_attachment(
    task_id: str,
    attachment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    t = await _load_task(db, me.tenant_id, task_id)
    if not _can_view_task(me, t):
        raise HTTPException(403, "Not allowed")
    att = (
        await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.task_id == task_id,
                Attachment.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not att:
        raise HTTPException(404, "Attachment not found")
    try:
        content = await attachment_storage.read(att.storage_path)
    except FileNotFoundError:
        raise HTTPException(404, "Stored file is missing")
    return Response(
        content=content,
        media_type=att.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{att.filename}"'},
    )


@router.delete("/{task_id}/attachments/{attachment_id}", status_code=204)
async def delete_task_attachment(
    task_id: str,
    attachment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """The uploader, or an admin, may delete it."""
    att = (
        await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.task_id == task_id,
                Attachment.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not att:
        raise HTTPException(404, "Attachment not found")
    if att.uploaded_by_id != me.id and not is_admin(me):
        raise HTTPException(403, "Only the uploader or an admin may delete this attachment")
    await attachment_storage.delete(att.storage_path)
    await db.delete(att)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ATTACHMENT_DELETED",
        entity_type="Attachment",
        entity_id=attachment_id,
        meta={"task_id": task_id},
    )
    await db.commit()


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a task.

    Permission matrix:
      * Admin / plan owner / lead-with-entity-scope: any field.
      * Team Member who is the assignee: status ONLY (execution-only).
      * Anyone else: 403.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update")

    if is_admin(me) or is_team_lead(me):
        # Structural authority — enforced via plan ownership / entity scope.
        await ensure_lead_edit_plan(db, me, plan)
    elif is_team_member(me):
        if t.assigned_to_id != me.id and t.member_assigned_to_id != me.id:
            raise HTTPException(403, "Not allowed")
        ensure_member_update_allowed(
            me,
            set(changes.keys()),
            TASK_MEMBER_ALLOWED_FIELDS,
            resource_label="task",
        )
    else:
        raise HTTPException(403, "Not allowed")

    if payload.title is not None:
        t.title = payload.title
    if payload.description is not None:
        t.description = payload.description
    if payload.priority is not None:
        t.priority = payload.priority
    if payload.tags is not None:
        t.tags = payload.tags
    if "due_time" in changes:
        t.due_time = payload.due_time

    if "assigned_to_id" in changes:
        ensure_admin_task_assignment_allowed(me)
        t.assigned_to_id = payload.assigned_to_id
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_ASSIGNED",
            entity_type="Task",
            entity_id=t.id,
            meta={"assigned_to_id": payload.assigned_to_id},
        )
        if payload.assigned_to_id:
            if t.status == TaskStatus.NOT_STARTED:
                t.status = TaskStatus.ASSIGNED
            await db.execute(
                update(SubTask)
                .where(
                    SubTask.task_id == t.id, SubTask.status == TaskStatus.NOT_STARTED
                )
                .values(
                    assigned_to_id=payload.assigned_to_id,
                    status=TaskStatus.ASSIGNED,
                )
            )
        elif t.status == TaskStatus.ASSIGNED:
            t.status = TaskStatus.NOT_STARTED
        if payload.assigned_to_id and payload.assigned_to_id != me.id:
            await notify_user_task_assigned(
                db, me.tenant_id, payload.assigned_to_id, t.id, t.title
            )
    if "member_assigned_to_id" in changes:
        if await _task_has_subtasks(db, t.id):
            raise HTTPException(
                400,
                "This task has sub-tasks; assign Team Members on the "
                "sub-tasks instead.",
            )
        if t.status == TaskStatus.COMPLETED:
            raise HTTPException(400, "Cannot reassign a completed task")
        if payload.member_assigned_to_id:
            member = (
                await db.execute(
                    select(User).where(
                        User.id == payload.member_assigned_to_id,
                        User.tenant_id == me.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            is_self = payload.member_assigned_to_id == me.id
            if not member or (member.role != Role.TEAM_MEMBER and not is_self):
                raise HTTPException(
                    400, "member_assigned_to_id must be a Team Member in this tenant"
                )
        # This field is the single-member-mode interface — any explicit
        # change to it (including clearing it) switches the task out of
        # whole-entity mode and drops the per-member completion rows.
        was_entity_wide = t.assigned_entity_wide
        old_member_id = t.member_assigned_to_id
        if t.assigned_entity_wide:
            for a in list(t.entity_assignees):
                t.entity_assignees.remove(a)
                await db.delete(a)
            t.assigned_entity_wide = False
        t.member_assigned_to_id = payload.member_assigned_to_id
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_MEMBER_ASSIGNED",
            entity_type="Task",
            entity_id=t.id,
            meta={"member_assigned_to_id": payload.member_assigned_to_id},
        )
        if payload.member_assigned_to_id:
            # A genuine hand-off to someone new (or a mode switch out of
            # whole-entity) — the previous assignee's progress/submission
            # doesn't carry over to whoever picks it up next.
            if was_entity_wide or (old_member_id and old_member_id != payload.member_assigned_to_id):
                t.status = TaskStatus.ASSIGNED
                t.rejection_note = None
                t.completion_notes = None
                t.completion_time_minutes = None
                t.submitted_by_id = None
                t.submitted_at = None
            elif t.status == TaskStatus.NOT_STARTED:
                t.status = TaskStatus.ASSIGNED
        elif was_entity_wide or old_member_id:
            # Explicitly unassigned (single or whole-entity) — nobody
            # executes this leaf task anymore, so it goes back to
            # NOT_STARTED rather than staying stuck in whatever in-flight
            # status its now-departed assignee left it in (mirrors the
            # plain `assigned_to_id` field's own clear behavior above).
            t.status = TaskStatus.NOT_STARTED
            t.rejection_note = None
            t.completion_notes = None
            t.completion_time_minutes = None
            t.submitted_by_id = None
            t.submitted_at = None
        if payload.member_assigned_to_id and payload.member_assigned_to_id != me.id:
            await notify_user_task_assigned(
                db, me.tenant_id, payload.member_assigned_to_id, t.id, t.title
            )
    if "due_date" in changes or "due_date_type" in changes or "post_period_due_days" in changes:
        # `due_date: null` is an explicit "clear the deadline" — always
        # allowed, no window check. A real date must land within the
        # task's own due_date_type/post_period_due_days window against its
        # plan's period — see services/due_date_rules.py. Any field left
        # out of this PATCH keeps its current value.
        new_due = payload.due_date if "due_date" in changes else t.due_date
        new_type = payload.due_date_type if payload.due_date_type is not None else t.due_date_type
        new_days = (
            payload.post_period_due_days
            if payload.post_period_due_days is not None
            else t.post_period_due_days
        )
        if new_due is not None:
            period = await _load_period(db, me.tenant_id, plan.period_id)
            try:
                # Unchanged re-submits (incl. a legacy weekend or since-added holiday date) pass.
                validate_working_day(new_due, current=t.due_date, label="Task")
                validate_due_date(
                    period_start=period.start_date, period_end=period.end_date,
                    due_date=new_due, due_date_type=new_type,
                    post_period_due_days=new_days,
                )
            except DueDateOutOfRangeError as e:
                raise HTTPException(400, str(e))
        t.due_date = new_due
        t.due_date_type = new_type
        t.post_period_due_days = new_days
    if payload.status is not None:
        if payload.status == TaskStatus.COMPLETED:
            raise HTTPException(
                400,
                "A task can't be completed directly — submit it for review "
                "via POST /tasks/{id}/submit-for-review, then have a Team "
                "Lead approve it via POST /tasks/{id}/review (or, if it has "
                "sub-tasks, complete those instead).",
            )
        old = t.status
        t.status = payload.status
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_STATUS_CHANGED",
            entity_type="Task",
            entity_id=t.id,
            meta={"from": old.value, "to": payload.status.value},
        )

    # rollup plan
    await recompute_plan(db, plan)

    await db.commit()
    t = await _load_task(db, me.tenant_id, task_id)
    return await _build_task_out(db, me.tenant_id, t)


def _entity_completion_reached(t: Task) -> bool:
    """Whether a multi-assignee task's assignee rows satisfy its completion
    rule: an OPTIONAL task (is_required=False) needs just ONE assignee done;
    a mandatory one needs every assignee done (unchanged behavior). Scoped
    to this Task row, which is already one entity + period + date instance."""
    rows = t.entity_assignees
    if not rows:
        return False
    if t.is_required is False:
        return any(a.done for a in rows)
    return all(a.done for a in rows)


async def _apply_multi_assignment(
    db: AsyncSession,
    me: User,
    t: Task,
    plan: Plan,
    member_ids: set[str],
    *,
    log_action_name: str,
    log_meta: dict,
    notify: bool = True,
    due_dates: dict[str, date | None] | None = None,
) -> set[str]:
    """
    Shared fan-out logic behind both "assign to whole entity" and "assign
    to specific people" — a task's status column is never allowed to
    diverge from what its TaskEntityAssignee rows actually show.

    Reassignment is allowed any time the task isn't COMPLETED, including
    while it's PENDING_REVIEW (e.g. swapping out someone who left before
    finishing their part) — so membership changes made at that point
    recompute the status back down to IN_PROGRESS whenever the new set
    isn't already fully done, instead of leaving a stale PENDING_REVIEW.

    `due_dates` (only ever passed from the hand-picked "assign to
    specific people" path — a whole-entity fan-out has no per-name step
    to attach one to) optionally gives one or more assignees their own
    deadline, capped at the task's own due date; applied to both newly
    created rows and rows that already existed (so reopening the
    assignee picker can add/change/clear a date without touching who's
    assigned).
    """
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may assign tasks")
    await ensure_lead_edit_plan(db, me, plan)

    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    if await _task_has_subtasks(db, t.id):
        raise HTTPException(
            400,
            "This task has sub-tasks; assign Team Members on the "
            "sub-tasks instead.",
        )
    if t.status == TaskStatus.COMPLETED:
        raise HTTPException(400, "Cannot reassign a completed task")
    if not member_ids:
        raise HTTPException(400, "Select at least one person to assign this task to")

    if due_dates:
        for uid, dd in due_dates.items():
            if uid not in member_ids:
                raise HTTPException(
                    400, f"Got a due date for {uid}, who isn't being assigned"
                )
            if dd is not None and t.due_date is not None and dd > t.due_date:
                raise HTTPException(
                    400,
                    f"An assignee's deadline ({dd.isoformat()}) can't be after "
                    f"the task's own due date ({t.due_date.isoformat()})",
                )
            try:
                validate_working_day(
                    dd,
                    current=next(
                        (a.due_date for a in t.entity_assignees if a.user_id == uid), None
                    ),
                    label="Assignee",
                )
            except DueDateOutOfRangeError as e:
                raise HTTPException(400, str(e))

    t.member_assigned_to_id = None
    t.assigned_entity_wide = True

    existing = {a.user_id: a for a in t.entity_assignees}
    # Drop assignees no longer in scope, as long as they haven't already
    # marked done (accountability — don't erase recorded completion).
    for uid, a in list(existing.items()):
        if uid not in member_ids and not a.done:
            t.entity_assignees.remove(a)
            await db.delete(a)
            del existing[uid]
    newly_added: set[str] = set()
    for uid in member_ids:
        if uid not in existing:
            a = TaskEntityAssignee(
                tenant_id=me.tenant_id, task_id=t.id, user_id=uid,
                due_date=(due_dates or {}).get(uid),
            )
            db.add(a)
            t.entity_assignees.append(a)
            newly_added.add(uid)
        elif due_dates and uid in due_dates:
            existing[uid].due_date = due_dates[uid]

    if t.status == TaskStatus.NOT_STARTED:
        t.status = TaskStatus.ASSIGNED
    elif t.status == TaskStatus.PENDING_REVIEW and not _entity_completion_reached(t):
        # Membership changed after everyone had finished — it's no longer
        # true that "everyone is done", so pull it back out of the Lead's
        # review queue until the (new) full set has actually finished.
        t.status = TaskStatus.IN_PROGRESS

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action=log_action_name,
        entity_type="Task",
        entity_id=t.id,
        meta=log_meta,
    )
    if notify:
        for uid in newly_added:
            if uid != me.id:
                await notify_user_task_assigned(db, me.tenant_id, uid, t.id, t.title)
    return newly_added


@router.post("/{task_id}/assign-entity", response_model=TaskOut)
async def assign_task_to_entity(
    task_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Whole-entity assignment for a LEAF task (no sub-tasks).

    Fans the task out to every TEAM_MEMBER scoped to the task's entity
    (primary_entity_id match, or an explicit secondary entity assignment) —
    each gets a TaskEntityAssignee row. Every one of them must mark
    themselves done via POST /tasks/{id}/mark-done-entity; the task only
    auto-submits for the Lead's review once every row is done. Mutually
    exclusive with member_assigned_to_id (single-member mode) — calling
    this clears that field. Calling this again re-syncs membership to
    whoever currently matches the entity (a reassignment, not just an
    initial assignment) as long as the task isn't COMPLETED yet.

    Allowed callers: admin/lead with structural authority over the plan.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    member_ids = await _entity_member_ids(db, me.tenant_id, t.entity_id)
    if not member_ids:
        raise HTTPException(400, "No Team Members are scoped to this entity")

    await _apply_multi_assignment(
        db, me, t, plan, member_ids,
        log_action_name="TASK_ENTITY_ASSIGNED",
        log_meta={"entity_id": t.entity_id, "member_count": len(member_ids)},
    )
    await db.commit()
    t = await _load_task(db, me.tenant_id, task_id)
    return await _build_task_out(db, me.tenant_id, t)


@router.post("/{task_id}/assign-multi", response_model=TaskOut)
async def assign_task_multi(
    task_id: str,
    payload: TaskMultiAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign a LEAF task (no sub-tasks) to a specific, hand-picked set of
    people — same multi-assignee mechanics as POST .../assign-entity
    (every one of them must mark-done-entity; the task auto-submits once
    all have), just without requiring the set to be "everyone on the
    entity". Each id must be a Team Member in this tenant, or the caller
    themselves. Calling this again with a different list reassigns —
    allowed any time the task isn't COMPLETED.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    member_ids = set(payload.user_ids)
    if member_ids:
        rows = await db.execute(
            select(User).where(User.id.in_(member_ids), User.tenant_id == me.tenant_id)
        )
        found = {u.id: u for u in rows.scalars().all()}
        for uid in member_ids:
            u = found.get(uid)
            if not u or (u.role != Role.TEAM_MEMBER and uid != me.id):
                raise HTTPException(
                    400, f"user_id {uid} must be a Team Member in this tenant"
                )

    await _apply_multi_assignment(
        db, me, t, plan, member_ids,
        log_action_name="TASK_MULTI_ASSIGNED",
        log_meta={"member_count": len(member_ids)},
        due_dates=payload.due_dates,
    )
    await db.commit()
    t = await _load_task(db, me.tenant_id, task_id)
    return await _build_task_out(db, me.tenant_id, t)


async def _assign_single_member_to_task(
    db: AsyncSession, me: User, t: Task, member_id: str | None, *,
    notify: bool = True, series: bool = True,
) -> None:
    """Point one leaf task at a single Team Member (mirror of the
    member_assigned_to_id branch in update_task). The caller has already
    confirmed the task is a leaf and not COMPLETED, validated `member_id`,
    and holds structural authority over the plan. Does NOT commit."""
    was_entity_wide = t.assigned_entity_wide
    old_member_id = t.member_assigned_to_id
    if t.assigned_entity_wide:
        for a in list(t.entity_assignees):
            t.entity_assignees.remove(a)
            await db.delete(a)
        t.assigned_entity_wide = False
    t.member_assigned_to_id = member_id
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TASK_MEMBER_ASSIGNED",
        entity_type="Task",
        entity_id=t.id,
        meta={"member_assigned_to_id": member_id, "series": series},
    )
    if member_id:
        if was_entity_wide or (old_member_id and old_member_id != member_id):
            t.status = TaskStatus.ASSIGNED
            t.rejection_note = None
            t.completion_notes = None
            t.completion_time_minutes = None
            t.submitted_by_id = None
            t.submitted_at = None
        elif t.status == TaskStatus.NOT_STARTED:
            t.status = TaskStatus.ASSIGNED
    elif was_entity_wide or old_member_id:
        # Explicitly unassigned — nobody executes this leaf task anymore,
        # so it goes back to NOT_STARTED rather than staying stuck in
        # whatever in-flight status its now-departed assignee left it in
        # (mirrors the inline member_assigned_to_id branch in update_task).
        t.status = TaskStatus.NOT_STARTED
        t.rejection_note = None
        t.completion_notes = None
        t.completion_time_minutes = None
        t.submitted_by_id = None
        t.submitted_at = None
    if notify and member_id and member_id != me.id:
        await notify_user_task_assigned(db, me.tenant_id, member_id, t.id, t.title)


@router.post("/{task_id}/assign-series", response_model=TaskSeriesAssignOut)
async def assign_task_series(
    task_id: str,
    payload: TaskAssignSeriesIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign (or clear) EVERY occurrence of a recurring task (same series,
    same plan) at once — so a Lead doesn't have to reassign a daily task
    20 separate times, and unassigning one no longer means unassigning
    them all by hand either. Occurrences already COMPLETED or
    PENDING_REVIEW are left as-is (their in-flight work is preserved);
    occurrences that carry their own sub-tasks are skipped (members are
    assigned per sub-task there).

    0 ids → clears the assignee on every occurrence. One id → a plain
    single assignment on every occurrence (keeps the normal
    submit-for-review flow). Two or more → multi-assignee on every
    occurrence. Same permission + "reassignment resets progress" rules as
    the single-task endpoints.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may assign tasks")
    member_ids = set(payload.user_ids)
    await _validate_assignees(db, me, member_ids)

    result = await _assign_series_core(db, me, t, plan, member_ids)
    if result is None:
        raise HTTPException(
            400,
            "This task is not part of a recurring series (only one occurrence "
            "in this plan) — assign it directly instead.",
        )
    updated_ids, skipped = result

    # One notification per assignee for the whole batch, not one per row.
    if updated_ids:
        for uid in member_ids:
            if uid != me.id:
                await notify_user_task_assigned(db, me.tenant_id, uid, t.id, t.title)

    await recompute_plan(db, plan)
    await db.commit()

    fresh = await _load_task(db, me.tenant_id, task_id)
    return TaskSeriesAssignOut(
        updated=len(updated_ids),
        skipped=len(skipped),
        task=await _build_task_out(db, me.tenant_id, fresh),
    )


async def _validate_assignees(db: AsyncSession, me: User, member_ids: set[str]) -> None:
    """Every id must be a Team Member in this tenant, or the caller themselves."""
    if not member_ids:
        return
    found_rows = await db.execute(
        select(User).where(User.id.in_(member_ids), User.tenant_id == me.tenant_id)
    )
    found = {u.id: u for u in found_rows.scalars().all()}
    for uid in member_ids:
        u = found.get(uid)
        if not u or (u.role != Role.TEAM_MEMBER and uid != me.id):
            raise HTTPException(
                400, f"user_id {uid} must be a Team Member in this tenant"
            )


def _series_filter(t: Task):
    """SQL filter matching every occurrence of `t`'s recurring series, or
    None when `t` isn't part of one."""
    if t.recurring_group_id:
        return Task.recurring_group_id == t.recurring_group_id
    if t.source_template_id:
        return Task.source_template_id == t.source_template_id
    return None


async def _assign_series_core(
    db: AsyncSession, me: User, t: Task, plan: Plan, member_ids: set[str],
    *, log_series: bool = True,
) -> tuple[set[str], dict[str, str]] | None:
    """Assign (or clear, for 0 ids) every occurrence of `t`'s series in its
    plan + entity. Returns (updated task ids, {skipped task id: reason}), or
    None when `t` isn't part of a multi-occurrence series (nothing done).
    Raises HTTPException for permission / closed-plan problems. Occurrences
    that are COMPLETED / PENDING_REVIEW or carry sub-tasks are skipped.
    Does NOT notify, recompute the plan, or commit — callers do."""
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may assign tasks")
    await ensure_lead_edit_plan(db, me, plan)
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")

    series_filter = _series_filter(t)
    if series_filter is None:
        return None

    sib_rows = await db.execute(
        select(Task)
        .where(
            Task.tenant_id == me.tenant_id,
            Task.plan_id == t.plan_id,
            Task.entity_id == t.entity_id,
            series_filter,
        )
        .options(
            selectinload(Task.entity_assignees),
            selectinload(Task.sub_tasks),
        )
    )
    siblings = list(sib_rows.scalars().all())
    if len(siblings) <= 1:
        return None

    updated_ids: set[str] = set()
    skipped: dict[str, str] = {}
    for s in siblings:
        if s.status == TaskStatus.UPCOMING:
            skipped[s.id] = "In an upcoming period"
            continue
        if s.status in (TaskStatus.COMPLETED, TaskStatus.PENDING_REVIEW):
            skipped[s.id] = "Already completed or in review"
            continue
        if s.sub_tasks:
            skipped[s.id] = "Has sub-tasks — assign on the sub-tasks instead"
            continue
        if len(member_ids) >= 2:
            await _apply_multi_assignment(
                db, me, s, plan, member_ids,
                log_action_name="TASK_SERIES_ASSIGNED",
                log_meta={"member_count": len(member_ids), "series": True},
                notify=False,
            )
        else:
            # 0 ids -> None (clears this occurrence); 1 id -> that member.
            await _assign_single_member_to_task(
                db, me, s, next(iter(member_ids), None),
                notify=False, series=log_series,
            )
        updated_ids.add(s.id)
    return updated_ids, skipped


@router.post("/{task_id}/mark-done-entity", response_model=TaskOut)
async def mark_task_done_entity(
    task_id: str,
    payload: TaskEntityDoneIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Completion action for ONE assignee on a task assigned to multiple
    people (whole-entity or a hand-picked multi-assign, Team Lead
    included when they assigned it to themselves as one of several).

    Marks only the caller's own row done — for a mandatory task nobody's
    individual action completes it by itself; for an OPTIONAL one
    (is_required=False) the first assignee to finish completes it for the
    entity. Once every TaskEntityAssignee row for
    this task is done, the task auto-submits for the Team Lead's review
    (mirrors submit-for-review), exactly like the single-member flow.
    Time Taken and Notes are mandatory on every row — a shared task gets
    the same accountability trail from each person, not just whoever's
    solo-assigned. Idempotent: a no-op if the caller already marked done.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    if not t.assigned_entity_wide:
        raise HTTPException(400, "This task is not assigned to the whole entity")
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    if t.status == TaskStatus.COMPLETED:
        raise HTTPException(400, "Task is already completed.")

    mine = next((a for a in t.entity_assignees if a.user_id == me.id), None)
    if not mine:
        raise HTTPException(403, "You are not assigned to this task")
    if (
        t.is_required is False
        and t.status == TaskStatus.PENDING_REVIEW
        and not mine.done
    ):
        # Optional task: another assignee already completed it for the entity.
        raise HTTPException(400, "This optional task was already completed by another assignee.")

    if not mine.done:
        # TaskEntityDoneIn's own validators already guarantee non-blank
        # notes and a positive minute count — nothing further to check.
        mine.done = True
        mine.done_at = datetime.now(timezone.utc)
        mine.completion_notes = payload.completion_notes
        mine.completion_time_minutes = payload.completion_time_minutes
        await record_completion_time_entry(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            entity_id=t.entity_id,
            period_id=plan.period_id,
            minutes=payload.completion_time_minutes,
            task_id=t.id,
        )
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_ENTITY_MEMBER_DONE",
            entity_type="Task",
            entity_id=t.id,
        )

        if t.status in (TaskStatus.NOT_STARTED, TaskStatus.ASSIGNED, TaskStatus.REJECTED):
            t.status = TaskStatus.IN_PROGRESS

        all_done = _entity_completion_reached(t)
        if all_done and t.status != TaskStatus.PENDING_REVIEW:
            t.rejection_note = None
            t.status = TaskStatus.PENDING_REVIEW
            await log_action(
                db,
                tenant_id=me.tenant_id,
                user_id=me.id,
                action="TASK_SUBMITTED",
                entity_type="Task",
                entity_id=t.id,
                meta={"via": "entity-all-done"},
            )
            reviewer_id = _resolve_task_reviewer(t, plan)
            if reviewer_id and reviewer_id != me.id:
                await notify_user_task_submitted_for_review(
                    db, me.tenant_id, reviewer_id, t.id, t.title
                )

        await recompute_plan(db, plan)
        await db.commit()
        t = await _load_task(db, me.tenant_id, task_id)

    return await _build_task_out(db, me.tenant_id, t)


@router.post("/{task_id}/submit-for-review", response_model=TaskOut)
async def submit_task_for_review(
    task_id: str,
    payload: TaskSubmitIn | None = None,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Team-Member completion action for a LEAF task (no sub-tasks).

    Pushes the task into PENDING_REVIEW — the Team Lead approves or
    rejects it via POST /tasks/{id}/review, mirroring the sub-task
    approval flow. Tasks that have sub-tasks can never be submitted here;
    their status is derived automatically via rollup.

    Allowed callers: the task's member_assigned_to_id (or the legacy
    assigned_to_id, for ad-hoc tasks a Lead assigned directly to a
    member), or an admin/lead with structural authority over the plan.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    if t.assigned_entity_wide:
        # Multi-assignee tasks (whole-entity or a hand-picked subset) are
        # never submitted as a single action — every assignee, including a
        # Team Lead who assigned the task to themselves as one of several
        # people, marks their OWN part done via mark-done-entity; the task
        # only moves to review once everyone's row is done.
        raise HTTPException(
            400,
            "This task is assigned to multiple people — use "
            "POST /tasks/{id}/mark-done-entity to mark your own part done.",
        )

    is_assignee = me.id in (t.assigned_to_id, t.member_assigned_to_id)
    if not is_assignee and not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only the assignee or a lead/admin may submit this")
    if (is_admin(me) or is_team_lead(me)) and not is_assignee:
        await ensure_lead_edit_plan(db, me, plan)

    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    if await _task_has_subtasks(db, t.id):
        raise HTTPException(
            400,
            "This task has sub-tasks; complete each sub-task instead — "
            "the task will complete automatically.",
        )
    if t.status == TaskStatus.COMPLETED:
        raise HTTPException(400, "Task is already completed.")

    if t.status != TaskStatus.PENDING_REVIEW:
        # A Team Lead/Admin who assigned this task to THEMSELVES is also
        # the one who'd review it — skip the round trip through
        # PENDING_REVIEW and complete it in one step instead of making
        # them submit-then-immediately-approve their own work. Since
        # there's no separate review step to catch a missing value
        # later, Time Taken and Notes are mandatory for this path.
        self_reviewable = is_assignee and (is_admin(me) or is_team_lead(me))
        if self_reviewable:
            notes, minutes = ensure_completion_details(
                payload.completion_notes if payload else None,
                payload.completion_time_minutes if payload else None,
            )
        else:
            notes = (payload.completion_notes or "").strip() or None if payload else None
            minutes = payload.completion_time_minutes if payload else None

        t.rejection_note = None
        t.completion_notes = notes
        t.completion_time_minutes = minutes
        t.submitted_by_id = me.id
        t.submitted_at = datetime.now(timezone.utc)
        t.status = TaskStatus.COMPLETED if self_reviewable else TaskStatus.PENDING_REVIEW
        await record_completion_time_entry(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            entity_id=t.entity_id,
            period_id=plan.period_id,
            minutes=minutes,
            task_id=t.id,
        )
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_COMPLETED" if self_reviewable else "TASK_SUBMITTED",
            entity_type="Task",
            entity_id=t.id,
        )
        if not self_reviewable:
            reviewer_id = _resolve_task_reviewer(t, plan)
            if reviewer_id and reviewer_id != me.id:
                await notify_user_task_submitted_for_review(
                    db, me.tenant_id, reviewer_id, t.id, t.title
                )
        await recompute_plan(db, plan)
        await db.commit()
        t = await _load_task(db, me.tenant_id, task_id)

    return await _build_task_out(db, me.tenant_id, t)


@router.post("/{task_id}/review", response_model=TaskOut)
async def review_task(
    task_id: str,
    payload: TaskReviewIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Team-Lead approval for a LEAF task pending review (mirrors
    POST /subtasks/{id}/review). Any Team Lead or Super Admin may
    approve/reject — there is no separate reviewer role, same as the
    sub-task flow. NOTE: `assigned_to_id` is deliberately NOT
    treated as a reviewer here — on ad-hoc tasks a Lead creates directly,
    that field holds the executing Team Member, and letting it satisfy
    this check would let a member approve their own submission.
    """
    t = await _load_task(db, me.tenant_id, task_id)
    _ensure_not_upcoming(t)
    plan = await _load_plan(db, me.tenant_id, t.plan_id)

    await _review_task_core(db, me, t, plan, payload)
    await recompute_plan(db, plan)
    await db.commit()
    t = await _load_task(db, me.tenant_id, task_id)
    return await _build_task_out(db, me.tenant_id, t)


async def _review_task_core(
    db: AsyncSession, me: User, t: Task, plan: Plan, payload: TaskReviewIn
) -> None:
    """Validate + apply one approve/reject decision on a leaf task and queue
    its notifications. Shared by POST /{id}/review and POST /bulk-review.
    All checks run before any mutation, so a raised HTTPException leaves the
    task untouched. Does NOT recompute the plan or commit — callers do."""
    is_super = me.role == Role.SUPER_ADMIN
    is_lead = me.role == Role.TEAM_LEAD
    if not (is_super or is_lead):
        raise HTTPException(403, "Only a Team Lead or Super Admin may review")

    if await _task_has_subtasks(db, t.id):
        raise HTTPException(
            400,
            "This task has sub-tasks; review each sub-task instead — "
            "the task's status rolls up from them automatically.",
        )

    if t.status != TaskStatus.PENDING_REVIEW:
        raise HTTPException(400, "Task is not pending review")

    decision = (payload.decision or "").upper().strip()
    if decision == "APPROVE":
        t.status = TaskStatus.COMPLETED
        t.rejection_note = None
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_APPROVED",
            entity_type="Task",
            entity_id=t.id,
        )
    elif decision == "REJECT":
        if not (payload.note or "").strip():
            raise HTTPException(400, "Rejection requires a note explaining why")
        t.status = TaskStatus.REJECTED
        t.rejection_note = payload.note.strip()
        if t.assigned_entity_wide:
            # Sent back for rework — every entity member must re-mark done.
            for a in t.entity_assignees:
                a.done = False
                a.done_at = None
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="TASK_REJECTED",
            entity_type="Task",
            entity_id=t.id,
            meta={"note": payload.note},
        )
    else:
        raise HTTPException(400, "decision must be APPROVE or REJECT")

    recipients: set[str] = set()
    if t.submitted_by_id:
        recipients.add(t.submitted_by_id)
    elif t.assigned_entity_wide:
        recipients.update(a.user_id for a in t.entity_assignees)
    elif t.member_assigned_to_id:
        recipients.add(t.member_assigned_to_id)
    elif t.assigned_to_id:
        recipients.add(t.assigned_to_id)
    recipients.discard(me.id)
    for uid in recipients:
        if decision == "APPROVE":
            await notify_user_task_approved(db, me.tenant_id, uid, t.id, t.title)
        else:
            await notify_user_task_rejected(
                db, me.tenant_id, uid, t.id, t.title, payload.note
            )


# ============================================================
# Bulk actions (Team Lead) — assign / unassign / approve / reject
# ============================================================
# Each selected task runs in its own SAVEPOINT: one that can't be actioned
# (completed, has sub-tasks, plan closed, ...) is reported back in `skipped`
# with the reason, and never fails or rolls back the rest of the batch.
# Only plain ids/titles are kept across savepoints — a rolled-back savepoint
# can expire loaded ORM objects, and touching those in async code raises.


def _skip_reason(e: HTTPException) -> str:
    return e.detail if isinstance(e.detail, str) else "Not allowed"


@router.post("/bulk-assign", response_model=TaskBulkResultOut)
async def bulk_assign_tasks(
    payload: TaskBulkAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign (or, with no `user_ids`, unassign) many tasks at once. Team Lead
    only.

    scope "occurrence": exactly the selected rows change, using the same
    rules as POST /{id}/assign-multi (assign) / clearing the assignee
    (unassign).
    scope "series": every selected recurring task's whole series changes
    (same rules as POST /{id}/assign-series), run ONCE per series however
    many of its occurrences were selected; a task that isn't part of a
    multi-occurrence series falls back to "occurrence".

    Unassign leaves COMPLETED / PENDING_REVIEW tasks alone (their in-flight
    work is preserved) — they come back in `skipped`.
    """
    if not is_team_lead(me):
        raise HTTPException(403, "Only a Team Lead may bulk-assign tasks")
    member_ids = set(payload.user_ids)
    await _validate_assignees(db, me, member_ids)

    selected = set(payload.task_ids)
    updated_ids: set[str] = set()
    skipped: dict[str, TaskBulkSkipOut] = {}
    handled: set[str] = set()
    affected_plan_ids: set[str] = set()
    first_updated: tuple[str, str] | None = None  # (task id, title)

    def _skip(tid: str, title: str | None, reason: str) -> None:
        skipped[tid] = TaskBulkSkipOut(task_id=tid, title=title, reason=reason)

    for tid in payload.task_ids:
        if tid in handled:
            continue
        title: str | None = None
        try:
            async with db.begin_nested():
                t = await _load_task(db, me.tenant_id, tid)
                title = t.title
                _ensure_not_upcoming(t)
                plan = await _load_plan(db, me.tenant_id, t.plan_id)
                plan_id = plan.id

                if payload.scope == "series" and _series_filter(t) is not None:
                    result = await _assign_series_core(
                        db, me, t, plan, member_ids, log_series=False
                    )
                    if result is not None:
                        upd, skp = result
                        updated_ids |= upd
                        affected_plan_ids.add(plan_id)
                        for sid in selected:
                            if sid in upd:
                                handled.add(sid)
                            elif sid in skp:
                                handled.add(sid)
                                _skip(sid, None, skp[sid])
                        if upd and first_updated is None:
                            first_updated = (t.id, title)
                        continue

                # Occurrence scope (or a non-series task under series scope).
                if member_ids:
                    await _apply_multi_assignment(
                        db, me, t, plan, member_ids,
                        log_action_name="TASK_BULK_ASSIGNED",
                        log_meta={"member_count": len(member_ids), "bulk": True},
                        notify=False,
                    )
                else:
                    if not is_team_lead(me):
                        raise HTTPException(403, "Only a Team Lead may unassign tasks")
                    await ensure_lead_edit_plan(db, me, plan)
                    if plan.status == PlanStatus.CLOSED:
                        raise HTTPException(400, "Plan is closed")
                    if await _task_has_subtasks(db, t.id):
                        raise HTTPException(
                            400, "Has sub-tasks — unassign on the sub-tasks instead"
                        )
                    if t.status in (TaskStatus.COMPLETED, TaskStatus.PENDING_REVIEW):
                        raise HTTPException(400, "Already completed or in review")
                    await _assign_single_member_to_task(
                        db, me, t, None, notify=False, series=False
                    )
                updated_ids.add(tid)
                affected_plan_ids.add(plan_id)
                if first_updated is None:
                    first_updated = (tid, title)
        except HTTPException as e:
            _skip(tid, title, _skip_reason(e))

    # A selected task skipped by one pass but updated by another is not a skip.
    for tid in list(skipped):
        if tid in updated_ids:
            del skipped[tid]

    # One notification per assignee for the whole batch, not one per row.
    if updated_ids and member_ids and first_updated:
        for uid in member_ids:
            if uid != me.id:
                await notify_user_task_assigned(
                    db, me.tenant_id, uid, first_updated[0], first_updated[1]
                )

    for pid in affected_plan_ids:
        await recompute_plan(db, await _load_plan(db, me.tenant_id, pid))
    await db.commit()

    return TaskBulkResultOut(updated=len(updated_ids), skipped=list(skipped.values()))


@router.post("/bulk-review", response_model=TaskBulkResultOut)
async def bulk_review_tasks(
    payload: TaskBulkReviewIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve or reject many leaf tasks that are pending review. Team Lead
    only. Same per-task rules as POST /{id}/review; a task that can't be
    reviewed (not pending review, has sub-tasks, ...) is skipped with the
    reason. A rejection needs one `note`, applied to every rejected task.
    """
    if not is_team_lead(me):
        raise HTTPException(403, "Only a Team Lead may bulk-review tasks")
    decision = (payload.decision or "").upper().strip()
    if decision not in ("APPROVE", "REJECT"):
        raise HTTPException(400, "decision must be APPROVE or REJECT")
    note = (payload.note or "").strip() or None
    if decision == "REJECT" and not note:
        raise HTTPException(400, "Rejection requires a note explaining why")
    review = TaskReviewIn(decision=decision, note=note)

    updated = 0
    skipped: list[TaskBulkSkipOut] = []
    affected_plan_ids: set[str] = set()

    for tid in payload.task_ids:
        title: str | None = None
        try:
            async with db.begin_nested():
                t = await _load_task(db, me.tenant_id, tid)
                title = t.title
                _ensure_not_upcoming(t)
                plan = await _load_plan(db, me.tenant_id, t.plan_id)
                plan_id = plan.id
                await _review_task_core(db, me, t, plan, review)
            updated += 1
            affected_plan_ids.add(plan_id)
        except HTTPException as e:
            skipped.append(
                TaskBulkSkipOut(task_id=tid, title=title, reason=_skip_reason(e))
            )

    for pid in affected_plan_ids:
        await recompute_plan(db, await _load_plan(db, me.tenant_id, pid))
    await db.commit()

    return TaskBulkResultOut(updated=updated, skipped=skipped)
