"""Subtasks — maker/checker review flow. Always a leaf: completed directly
via submit-for-review + review, same as a leaf Task."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import (
    Attachment,
    Period,
    Plan,
    PlanStatus,
    Role,
    SubTask,
    SubTaskEntityAssignee,
    Task,
    TaskStatus,
    User,
)
from ..schemas import (
    AttachmentOut,
    SubTaskAssignSeriesIn,
    SubTaskCreate,
    SubTaskEntityDoneIn,
    SubTaskMultiAssignIn,
    SubTaskOut,
    SubTaskSeriesAssignOut,
    SubTaskUpdate,
    SubTaskReviewIn,
    SubTaskSubmitIn,
    UserMini,
)
from ..serialization import (
    subtask_entity_assignee_user_ids,
    subtask_entity_assignees_out,
)
from ..auth import require_tenant
from ..rollup import rollup_from_subtask
from ..services import attachment_storage
from ..services.access_service import (
    get_accessible_entity_ids,
    get_lead_direct_entity_ids,
)
from ..services.audit_service import log_action
from ..services.due_date_rules import (
    DueDateOutOfRangeError,
    validate_subtask_due_date,
    validate_working_day,
)
from ..services.notification_service import (
    notify_user_subtask_approved,
    notify_user_subtask_assigned,
    notify_user_subtask_rejected,
    notify_user_subtask_submitted_for_review,
)
from ..services.plan_customization_service import (
    SUBTASK_MEMBER_ALLOWED_FIELDS,
    can_member_see_subtask,
    ensure_completion_details,
    ensure_lead_edit_plan,
    ensure_member_update_allowed,
    is_admin,
    is_team_lead,
    is_team_member,
)
from ..services.time_entry_service import record_completion_time_entry
from .tasks import _entity_member_ids as entity_member_ids

router = APIRouter(prefix="/api/subtasks", tags=["subtasks"])


async def _load_subtask(db, tenant_id, sub_id) -> SubTask:
    s = (
        await db.execute(
            select(SubTask)
            .where(SubTask.id == sub_id, SubTask.tenant_id == tenant_id)
            .options(selectinload(SubTask.entity_assignees))
        )
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Sub-task not found")
    return s


def _ensure_not_upcoming(s: SubTask) -> None:
    """A sub-task in a future period is visible but locked."""
    if s.status == TaskStatus.UPCOMING:
        raise HTTPException(
            409,
            "This sub-task is in an upcoming period — it becomes actionable "
            "once that period is running.",
        )



async def _load_plan_for_subtask(
    db: AsyncSession, tenant_id: str, sub: SubTask
) -> Plan:
    """Resolve the plan that owns this subtask (subtask → task → plan)."""
    task = (
        await db.execute(
            select(Task).where(Task.id == sub.task_id, Task.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Parent task not found")
    plan = (
        await db.execute(
            select(Plan).where(Plan.id == task.plan_id, Plan.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Parent plan not found")
    return plan


async def _resolve_subtask_reviewer(
    db: AsyncSession, tenant_id: str, sub: SubTask, plan: Plan
) -> str | None:
    """Who should be notified when this subtask is submitted for review:
    its explicit reviewer, else the Team Lead who owns the parent task,
    else the plan owner."""
    if sub.reviewer_id:
        return sub.reviewer_id
    task = (
        await db.execute(
            select(Task).where(Task.id == sub.task_id, Task.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if task and task.assigned_to_id:
        return task.assigned_to_id
    return plan.owner_id


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
        updated_at=s.updated_at,
        assigned_to=users.get(s.assigned_to_id) if s.assigned_to_id else None,
        reviewer=users.get(s.reviewer_id) if s.reviewer_id else None,
        submitted_by=users.get(s.submitted_by_id) if s.submitted_by_id else None,
    )


def _serialize_attachment(
    a: Attachment, users: dict[str, UserMini]
) -> AttachmentOut:
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


def _can_view_subtask(me: User, s: SubTask) -> bool:
    """Same bar as GET /subtasks/{id}: a Team Member must be the
    assignee or reviewer; Admin/Lead can view any sub-task in the
    tenant."""
    if not is_team_member(me):
        return True
    return can_member_see_subtask(me, s)


async def _build_out(db: AsyncSession, s: SubTask) -> SubTaskOut:
    ids: set[str] = set()
    if s.assigned_to_id:
        ids.add(s.assigned_to_id)
    if s.reviewer_id:
        ids.add(s.reviewer_id)
    if s.submitted_by_id:
        ids.add(s.submitted_by_id)
    ids |= subtask_entity_assignee_user_ids(s)
    users = await _user_mini_map(db, s.tenant_id, ids)
    return _serialize_subtask(s, users)


@router.get("", response_model=list[SubTaskOut])
async def list_subtasks(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    task_id: str | None = None,
    assigned_to_id: str | None = None,
    status: TaskStatus | None = None,
    tag: str | None = None,
):
    filters = [SubTask.tenant_id == me.tenant_id]
    if task_id:
        filters.append(SubTask.task_id == task_id)
    if assigned_to_id:
        filters.append(SubTask.assigned_to_id == assigned_to_id)
    if status:
        filters.append(SubTask.status == status)
    rows = await db.execute(
        select(SubTask)
        .where(*filters)
        .order_by(SubTask.created_at)
        .options(selectinload(SubTask.entity_assignees))
    )
    subs = list(rows.scalars().all())
    if tag:
        needle = tag.strip().lower()
        subs = [s for s in subs if needle in (t.lower() for t in s.tags)]
    if is_team_member(me):
        # Tier-3 visibility: subtask must be directly assigned/reviewed by
        # me. Unassigned subtasks remain hidden.
        subs = [s for s in subs if can_member_see_subtask(me, s)]
    elif me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        if is_team_lead(me):
            # Strict lead visibility: direct entities (primary + secondary,
            # NO group expansion), plans they own, or tasks/subtasks
            # explicitly assigned to them.
            direct_entities = await get_lead_direct_entity_ids(db, me)
            owned_plan_rows = await db.execute(
                select(Plan.id).where(
                    Plan.tenant_id == me.tenant_id, Plan.owner_id == me.id
                )
            )
            owned_plan_ids = {pid for (pid,) in owned_plan_rows.all()}
            # Tasks that this lead has been assigned (their plans give
            # access too).
            lead_task_rows = await db.execute(
                select(Task.id, Task.plan_id).where(
                    Task.tenant_id == me.tenant_id,
                    Task.assigned_to_id == me.id,
                )
            )
            lead_task_ids: set[str] = set()
            lead_task_plan_ids: set[str] = set()
            for tid, pid in lead_task_rows.all():
                lead_task_ids.add(tid)
                lead_task_plan_ids.add(pid)

            owned_or_assigned_plan_ids = owned_plan_ids | lead_task_plan_ids
            if owned_or_assigned_plan_ids:
                task_rows = await db.execute(
                    select(Task.id).where(
                        Task.tenant_id == me.tenant_id,
                        Task.plan_id.in_(owned_or_assigned_plan_ids),
                    )
                )
                covered_task_ids = {tid for (tid,) in task_rows.all()}
            else:
                covered_task_ids = set()

            subs = [
                s
                for s in subs
                if s.entity_id in direct_entities
                or s.task_id in covered_task_ids
                or s.assigned_to_id == me.id
                or s.reviewer_id == me.id
            ]
        else:
            accessible = await get_accessible_entity_ids(db, me)
            subs = [s for s in subs if s.entity_id in accessible]

    ids: set[str] = set()
    for s in subs:
        if s.assigned_to_id:
            ids.add(s.assigned_to_id)
        if s.reviewer_id:
            ids.add(s.reviewer_id)
        if s.submitted_by_id:
            ids.add(s.submitted_by_id)
        ids |= subtask_entity_assignee_user_ids(s)
    users = await _user_mini_map(db, me.tenant_id, ids)
    return [_serialize_subtask(s, users) for s in subs]


@router.get("/{subtask_id}", response_model=SubTaskOut)
async def get_subtask(
    subtask_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    if is_team_member(me) and not can_member_see_subtask(me, s):
        raise HTTPException(403, "Not allowed")
    return await _build_out(db, s)


# ============================================================
# Attachments — files on this sub-task (see tasks.py for the task-level
# equivalent). Same visibility bar as viewing the sub-task: anyone who
# can see it can upload/list/download; only the uploader or an admin
# can delete.
# ============================================================
@router.get("/{subtask_id}/attachments", response_model=list[AttachmentOut])
async def list_subtask_attachments(
    subtask_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    if not _can_view_subtask(me, s):
        raise HTTPException(403, "Not allowed")
    rows = await db.execute(
        select(Attachment)
        .where(
            Attachment.tenant_id == me.tenant_id, Attachment.subtask_id == subtask_id
        )
        .order_by(Attachment.created_at)
    )
    atts = list(rows.scalars().all())
    users = await _user_mini_map(db, me.tenant_id, {a.uploaded_by_id for a in atts})
    return [_serialize_attachment(a, users) for a in atts]


@router.post(
    "/{subtask_id}/attachments", response_model=AttachmentOut, status_code=201
)
async def upload_subtask_attachment(
    subtask_id: str,
    file: UploadFile = File(...),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Lead/Admin at assignment time, or the executing Team Member later
    — anyone who can view this sub-task may attach a file to it."""
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    if not _can_view_subtask(me, s):
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
        subtask_id=subtask_id,
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
        meta={"subtask_id": subtask_id, "filename": safe_name},
    )
    await db.commit()
    await db.refresh(att)
    return _serialize_attachment(att, {me.id: UserMini(id=me.id, name=me.name, title=me.title)})


@router.get("/{subtask_id}/attachments/{attachment_id}/download")
async def download_subtask_attachment(
    subtask_id: str,
    attachment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    if not _can_view_subtask(me, s):
        raise HTTPException(403, "Not allowed")
    att = (
        await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.subtask_id == subtask_id,
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


@router.delete("/{subtask_id}/attachments/{attachment_id}", status_code=204)
async def delete_subtask_attachment(
    subtask_id: str,
    attachment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """The uploader, or an admin, may delete it."""
    att = (
        await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.subtask_id == subtask_id,
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
        meta={"subtask_id": subtask_id},
    )
    await db.commit()


@router.post("", response_model=SubTaskOut, status_code=201)
async def create_subtask(
    payload: SubTaskCreate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Add a custom subtask to an existing task (admin or plan-owning lead)."""
    if me.role not in (Role.SUPER_ADMIN, Role.TEAM_LEAD):
        raise HTTPException(403, "Only lead/admin may create custom subtasks")

    task = (
        await db.execute(
            select(Task).where(
                Task.id == payload.task_id, Task.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")

    plan = (
        await db.execute(
            select(Plan).where(Plan.id == task.plan_id, Plan.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Cannot add subtasks to a closed plan")

    await ensure_lead_edit_plan(db, me, plan)

    # Subtask deadline must fall within the plan's period window (per its
    # own due_date_type/post_period_due_days) AND on or before the parent
    # task's own deadline — see services/due_date_rules.py.
    if payload.due_date is not None:
        try:
            validate_working_day(payload.due_date, label="Sub-task")
        except DueDateOutOfRangeError as e:
            raise HTTPException(400, str(e))
        period = (
            await db.execute(
                select(Period).where(
                    Period.id == plan.period_id, Period.tenant_id == me.tenant_id
                )
            )
        ).scalar_one_or_none()
        if period:
            try:
                validate_subtask_due_date(
                    period_start=period.start_date, period_end=period.end_date,
                    due_date=payload.due_date, due_date_type=payload.due_date_type,
                    post_period_due_days=payload.post_period_due_days,
                    parent_task_due_date=task.due_date,
                )
            except DueDateOutOfRangeError as e:
                raise HTTPException(400, str(e))

    sub = SubTask(
        tenant_id=me.tenant_id,
        task_id=payload.task_id,
        entity_id=task.entity_id,
        source_template_id=None,
        is_custom=True,
        assigned_to_id=payload.assigned_to_id,
        reviewer_id=payload.reviewer_id,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
        due_date_type=payload.due_date_type,
        post_period_due_days=payload.post_period_due_days,
        due_time=payload.due_time,
        priority=payload.priority,
        tags=payload.tags,
        status=TaskStatus.ASSIGNED
        if payload.assigned_to_id
        else TaskStatus.NOT_STARTED,
    )
    db.add(sub)
    await db.flush()
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="SUBTASK_CREATED",
        entity_type="SubTask",
        entity_id=sub.id,
        meta={"task_id": task.id, "plan_id": plan.id, "is_custom": True},
    )
    if payload.assigned_to_id and payload.assigned_to_id != me.id:
        await notify_user_subtask_assigned(
            db, me.tenant_id, payload.assigned_to_id, sub.id, sub.title
        )
    await db.commit()
    sub = await _load_subtask(db, me.tenant_id, sub.id)
    return await _build_out(db, sub)


@router.patch("/{subtask_id}", response_model=SubTaskOut)
async def update_subtask(
    subtask_id: str,
    payload: SubTaskUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a subtask.

    Permission matrix:
      * Admin / plan owner / lead-with-entity-scope: any field.
      * Team Member assigned to the subtask: status ONLY.
      * Anyone else: 403.
    """
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    _ensure_not_upcoming(s)
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update")

    if is_admin(me) or is_team_lead(me):
        await ensure_lead_edit_plan(db, me, plan)
    elif is_team_member(me):
        if s.assigned_to_id != me.id:
            raise HTTPException(403, "Not allowed")
        ensure_member_update_allowed(
            me,
            set(changes.keys()),
            SUBTASK_MEMBER_ALLOWED_FIELDS,
            resource_label="subtask",
        )
    else:
        raise HTTPException(403, "Not allowed")

    if payload.title is not None:
        s.title = payload.title
    if payload.description is not None:
        s.description = payload.description
    if payload.priority is not None:
        s.priority = payload.priority
    if payload.tags is not None:
        s.tags = payload.tags
    if "due_time" in changes:
        s.due_time = payload.due_time
    if "assigned_to_id" in changes:
        if s.status == TaskStatus.COMPLETED:
            raise HTTPException(400, "Cannot reassign a completed subtask")
        old_assignee = s.assigned_to_id
        was_entity_wide = s.assigned_entity_wide
        # Switching to a single assignee (or clearing to none) exits
        # multi-assignee mode: drop every entity-assignee row that hasn't
        # already recorded completion (mirrors Task single-reassign).
        if s.assigned_entity_wide:
            for a in list(s.entity_assignees):
                if not a.done:
                    s.entity_assignees.remove(a)
                    await db.delete(a)
            s.assigned_entity_wide = False
        s.assigned_to_id = payload.assigned_to_id
        if payload.assigned_to_id:
            if was_entity_wide or (old_assignee and old_assignee != payload.assigned_to_id):
                # A genuine hand-off to someone new (or a mode switch out of
                # whole-entity) — the predecessor's progress/submission
                # doesn't carry over to whoever picks it up next, even if
                # that predecessor had it pending review.
                s.status = TaskStatus.ASSIGNED
                s.rejection_note = None
                s.completion_notes = None
                s.completion_time_minutes = None
                s.submitted_by_id = None
                s.submitted_at = None
            elif s.status in (TaskStatus.NOT_STARTED, TaskStatus.REJECTED):
                s.status = TaskStatus.ASSIGNED
            if payload.assigned_to_id != me.id:
                await notify_user_subtask_assigned(
                    db, me.tenant_id, payload.assigned_to_id, s.id, s.title
                )
        elif was_entity_wide or old_assignee:
            # Explicitly unassigned (single or whole-entity) — nobody is on
            # the hook for it anymore, so it goes back to NOT_STARTED
            # rather than staying stuck in whatever in-flight status its
            # now-departed assignee(s) left it in.
            s.status = TaskStatus.NOT_STARTED
            s.rejection_note = None
            s.completion_notes = None
            s.completion_time_minutes = None
            s.submitted_by_id = None
            s.submitted_at = None
    if payload.reviewer_id is not None:
        s.reviewer_id = payload.reviewer_id
    if "due_date" in changes or "due_date_type" in changes or "post_period_due_days" in changes:
        # `due_date: null` explicitly clears the deadline (no window check).
        # A real date must fall within the plan's period (per its own
        # due_date_type/post_period_due_days) AND on/before the parent
        # task's own due date — see services/due_date_rules.py. Any field
        # left out of this PATCH keeps its current value.
        new_due = payload.due_date if "due_date" in changes else s.due_date
        new_type = payload.due_date_type if payload.due_date_type is not None else s.due_date_type
        new_days = (
            payload.post_period_due_days
            if payload.post_period_due_days is not None
            else s.post_period_due_days
        )
        if new_due is not None:
            try:
                # Unchanged re-submits (incl. a legacy weekend or since-added holiday date) pass.
                validate_working_day(new_due, current=s.due_date, label="Sub-task")
            except DueDateOutOfRangeError as e:
                raise HTTPException(400, str(e))
            parent = (
                await db.execute(
                    select(Task).where(
                        Task.id == s.task_id, Task.tenant_id == me.tenant_id
                    )
                )
            ).scalar_one_or_none()
            period = (
                await db.execute(
                    select(Period).where(
                        Period.id == plan.period_id, Period.tenant_id == me.tenant_id
                    )
                )
            ).scalar_one_or_none()
            if period:
                try:
                    validate_subtask_due_date(
                        period_start=period.start_date, period_end=period.end_date,
                        due_date=new_due, due_date_type=new_type,
                        post_period_due_days=new_days,
                        parent_task_due_date=parent.due_date if parent else None,
                    )
                except DueDateOutOfRangeError as e:
                    raise HTTPException(400, str(e))
        s.due_date = new_due
        s.due_date_type = new_type
        s.post_period_due_days = new_days
    if payload.status is not None:
        if payload.status == TaskStatus.COMPLETED:
            raise HTTPException(
                400,
                "A sub-task can't be completed directly — submit it for "
                "review via POST /subtasks/{id}/submit-for-review, then "
                "have a Team Lead approve it via POST /subtasks/{id}/review.",
            )
        s.status = payload.status

    await rollup_from_subtask(db, s)
    await db.commit()
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    return await _build_out(db, s)


@router.delete("/{subtask_id}", status_code=204)
async def delete_subtask(
    subtask_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    if not s.is_custom:
        raise HTTPException(
            403,
            "Only custom subtasks may be deleted; template subtasks remain part of the plan",
        )
    if me.role not in (Role.SUPER_ADMIN, Role.TEAM_LEAD):
        raise HTTPException(403, "Not allowed")
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)
    await ensure_lead_edit_plan(db, me, plan)
    await db.delete(s)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="SUBTASK_DELETED",
        entity_type="SubTask",
        entity_id=subtask_id,
        meta={"plan_id": plan.id},
    )
    await db.commit()


@router.post("/{subtask_id}/submit-for-review", response_model=SubTaskOut)
async def submit_for_review(
    subtask_id: str,
    payload: SubTaskSubmitIn | None = None,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Team-Member completion action for a sub-task. A sub-task is always a
    leaf — directly submittable any time — this IS the sub-task's
    completion action. The Team Lead then approves/rejects via
    POST /subtasks/{id}/review.
    """
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    _ensure_not_upcoming(s)
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)
    if s.assigned_entity_wide:
        raise HTTPException(
            400,
            "This sub-task is assigned to multiple people — use "
            "POST /subtasks/{id}/mark-done-entity to mark your own part done.",
        )
    if me.id != s.assigned_to_id and me.role not in (Role.SUPER_ADMIN, Role.TEAM_LEAD):
        raise HTTPException(403, "Only assignee or a lead may submit")

    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    if s.status == TaskStatus.COMPLETED:
        raise HTTPException(400, "Sub-task is already completed.")

    if s.status != TaskStatus.PENDING_REVIEW:
        # A Team Lead/Admin who assigned this sub-task to THEMSELVES is
        # also the one who'd review it — skip PENDING_REVIEW and complete
        # it in one step instead of submit-then-immediately-approve. With
        # no separate review step to catch a missing value later, Time
        # Taken and Notes are mandatory for this path.
        self_reviewable = (
            me.id == s.assigned_to_id and me.role in (Role.SUPER_ADMIN, Role.TEAM_LEAD)
        )
        if self_reviewable:
            notes, minutes = ensure_completion_details(
                payload.completion_notes if payload else None,
                payload.completion_time_minutes if payload else None,
            )
        else:
            notes = (payload.completion_notes or "").strip() or None if payload else None
            minutes = payload.completion_time_minutes if payload else None

        s.rejection_note = None
        s.completion_notes = notes
        s.completion_time_minutes = minutes
        s.submitted_by_id = me.id
        s.submitted_at = datetime.now(timezone.utc)
        s.status = TaskStatus.COMPLETED if self_reviewable else TaskStatus.PENDING_REVIEW
        await rollup_from_subtask(db, s)
        await record_completion_time_entry(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            entity_id=s.entity_id,
            period_id=plan.period_id,
            minutes=minutes,
            subtask_id=s.id,
        )
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="SUBTASK_COMPLETED" if self_reviewable else "SUBTASK_SUBMITTED",
            entity_type="SubTask",
            entity_id=s.id,
        )
        if self_reviewable:
            reviewer_id = None
        else:
            reviewer_id = await _resolve_subtask_reviewer(db, me.tenant_id, s, plan)
        if reviewer_id and reviewer_id != me.id:
            await notify_user_subtask_submitted_for_review(
                db, me.tenant_id, reviewer_id, s.id, s.title
            )
        await db.commit()
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    return await _build_out(db, s)


@router.post("/{subtask_id}/review", response_model=SubTaskOut)
async def review_subtask(
    subtask_id: str,
    payload: SubTaskReviewIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    if s.status != TaskStatus.PENDING_REVIEW:
        raise HTTPException(400, "Sub-task is not pending review")

    is_reviewer = me.id == s.reviewer_id
    is_super = me.role == Role.SUPER_ADMIN
    is_lead = me.role == Role.TEAM_LEAD
    if not (is_reviewer or is_super or is_lead):
        raise HTTPException(403, "Only reviewer, lead, or Super Admin may review")

    decision = payload.decision.upper().strip()
    if decision == "APPROVE":
        s.status = TaskStatus.COMPLETED
        s.rejection_note = None
    elif decision == "REJECT":
        s.status = TaskStatus.REJECTED
        s.rejection_note = payload.note
        # A multi-assignee sub-task sent back: every assignee has to redo
        # and re-mark their own part (mirrors the whole-entity task flow).
        if s.assigned_entity_wide:
            for a in s.entity_assignees:
                a.done = False
                a.done_at = None
                a.completion_notes = None
                a.completion_time_minutes = None
    else:
        raise HTTPException(400, "decision must be APPROVE or REJECT")

    await rollup_from_subtask(db, s)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action=f"SUBTASK_{decision}D",
        entity_type="SubTask",
        entity_id=s.id,
        meta={"note": payload.note},
    )
    if s.submitted_by_id and s.submitted_by_id != me.id:
        if decision == "APPROVE":
            await notify_user_subtask_approved(
                db, me.tenant_id, s.submitted_by_id, s.id, s.title
            )
        else:
            await notify_user_subtask_rejected(
                db, me.tenant_id, s.submitted_by_id, s.id, s.title, payload.note
            )
    await db.commit()
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    return await _build_out(db, s)


# ============================================================
# Sub-task multi-assignment (whole-entity / hand-picked) — mirrors the
# task-level flow in routers/tasks.py (_apply_multi_assignment etc.).
# ============================================================
async def _apply_subtask_multi_assignment(
    db: AsyncSession,
    me: User,
    s: SubTask,
    plan: Plan,
    member_ids: set[str],
    *,
    log_action_name: str,
    log_meta: dict,
    notify: bool = True,
    due_dates: dict[str, date | None] | None = None,
) -> set[str]:
    """See _apply_multi_assignment (routers/tasks.py) — same shape, one
    level down. `due_dates` is capped at this sub-task's own due date."""
    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may assign sub-tasks")
    await ensure_lead_edit_plan(db, me, plan)
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    if s.status == TaskStatus.COMPLETED:
        raise HTTPException(400, "Cannot reassign a completed sub-task")
    if not member_ids:
        raise HTTPException(400, "Select at least one person to assign this sub-task to")

    if due_dates:
        for uid, dd in due_dates.items():
            if uid not in member_ids:
                raise HTTPException(
                    400, f"Got a due date for {uid}, who isn't being assigned"
                )
            if dd is not None and s.due_date is not None and dd > s.due_date:
                raise HTTPException(
                    400,
                    f"An assignee's deadline ({dd.isoformat()}) can't be after "
                    f"the sub-task's own due date ({s.due_date.isoformat()})",
                )
            try:
                validate_working_day(
                    dd,
                    current=next(
                        (a.due_date for a in s.entity_assignees if a.user_id == uid), None
                    ),
                    label="Assignee",
                )
            except DueDateOutOfRangeError as e:
                raise HTTPException(400, str(e))

    s.assigned_to_id = None
    s.assigned_entity_wide = True

    existing = {a.user_id: a for a in s.entity_assignees}
    for uid, a in list(existing.items()):
        if uid not in member_ids and not a.done:
            s.entity_assignees.remove(a)
            await db.delete(a)
            del existing[uid]
    newly_added: set[str] = set()
    for uid in member_ids:
        if uid not in existing:
            a = SubTaskEntityAssignee(
                tenant_id=me.tenant_id, subtask_id=s.id, user_id=uid,
                due_date=(due_dates or {}).get(uid),
            )
            db.add(a)
            s.entity_assignees.append(a)
            newly_added.add(uid)
        elif due_dates and uid in due_dates:
            existing[uid].due_date = due_dates[uid]

    if s.status == TaskStatus.NOT_STARTED:
        s.status = TaskStatus.ASSIGNED
    elif s.status == TaskStatus.PENDING_REVIEW and not all(
        a.done for a in s.entity_assignees
    ):
        s.status = TaskStatus.IN_PROGRESS

    await rollup_from_subtask(db, s)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action=log_action_name,
        entity_type="SubTask",
        entity_id=s.id,
        meta=log_meta,
    )
    if notify:
        for uid in newly_added:
            if uid != me.id:
                await notify_user_subtask_assigned(db, me.tenant_id, uid, s.id, s.title)
    return newly_added


@router.post("/{subtask_id}/assign-entity", response_model=SubTaskOut)
async def assign_subtask_to_entity(
    subtask_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Fan a sub-task out to every Team Member scoped to its entity — each
    gets a SubTaskEntityAssignee row and marks their own part done via
    POST /subtasks/{id}/mark-done-entity."""
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    _ensure_not_upcoming(s)
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)
    member_ids = await entity_member_ids(db, me.tenant_id, s.entity_id)
    if not member_ids:
        raise HTTPException(400, "No Team Members are scoped to this entity")
    await _apply_subtask_multi_assignment(
        db, me, s, plan, member_ids,
        log_action_name="SUBTASK_ENTITY_ASSIGNED",
        log_meta={"entity_id": s.entity_id, "member_count": len(member_ids)},
    )
    await db.commit()
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    return await _build_out(db, s)


@router.post("/{subtask_id}/assign-multi", response_model=SubTaskOut)
async def assign_subtask_multi(
    subtask_id: str,
    payload: SubTaskMultiAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Assign a sub-task to a specific, hand-picked set of 1+ people."""
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    _ensure_not_upcoming(s)
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)

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

    await _apply_subtask_multi_assignment(
        db, me, s, plan, member_ids,
        log_action_name="SUBTASK_MULTI_ASSIGNED",
        log_meta={"member_count": len(member_ids)},
        due_dates=payload.due_dates,
    )
    await db.commit()
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    return await _build_out(db, s)


async def _assign_single_member_to_subtask(
    db: AsyncSession, me: User, s: SubTask, member_id: str | None, *, notify: bool = True
) -> None:
    """Point one sub-task at a single Team Member (mirror of the
    assigned_to_id branch in update_subtask). The caller has already
    confirmed the sub-task isn't COMPLETED and holds structural authority
    over the plan. Does NOT commit."""
    old_assignee = s.assigned_to_id
    was_entity_wide = s.assigned_entity_wide
    if s.assigned_entity_wide:
        for a in list(s.entity_assignees):
            if not a.done:
                s.entity_assignees.remove(a)
                await db.delete(a)
        s.assigned_entity_wide = False
    s.assigned_to_id = member_id
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="SUBTASK_MEMBER_ASSIGNED",
        entity_type="SubTask",
        entity_id=s.id,
        meta={"assigned_to_id": member_id, "series": True},
    )
    if member_id:
        if was_entity_wide or (old_assignee and old_assignee != member_id):
            s.status = TaskStatus.ASSIGNED
            s.rejection_note = None
            s.completion_notes = None
            s.completion_time_minutes = None
            s.submitted_by_id = None
            s.submitted_at = None
        elif s.status in (TaskStatus.NOT_STARTED, TaskStatus.REJECTED):
            s.status = TaskStatus.ASSIGNED
    elif was_entity_wide or old_assignee:
        # Explicitly unassigned — nobody is on the hook for it anymore, so
        # it goes back to NOT_STARTED (mirrors the inline assigned_to_id
        # branch in update_subtask).
        s.status = TaskStatus.NOT_STARTED
        s.rejection_note = None
        s.completion_notes = None
        s.completion_time_minutes = None
        s.submitted_by_id = None
        s.submitted_at = None
    if notify and member_id and member_id != me.id:
        await notify_user_subtask_assigned(db, me.tenant_id, member_id, s.id, s.title)


@router.post("/{subtask_id}/assign-series", response_model=SubTaskSeriesAssignOut)
async def assign_subtask_series(
    subtask_id: str,
    payload: SubTaskAssignSeriesIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign EVERY occurrence of a recurring task's matching sub-task at
    once — the sub-task analogue of POST /tasks/{id}/assign-series.

    A task-level series-assign SKIPS any occurrence that carries
    sub-tasks (assignment for those lives on the sub-tasks instead), so a
    daily/weekly task built with sub-tasks previously had no bulk-assign
    at all — the Lead had to open every single occurrence and assign its
    sub-task one at a time. This closes that gap: it finds the SAME
    sub-task (matched by sub-task template, or by title when the parent
    is a custom recurring task with no template) on every sibling
    occurrence of the parent task's series, in this same plan, and
    assigns it there too.

    Occurrences already COMPLETED or PENDING_REVIEW are left as-is (their
    in-flight work is preserved). 0 ids -> clears the assignee on every
    occurrence; one id -> a plain single assignment on every occurrence;
    two or more -> multi-assignee on every occurrence.
    """
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    _ensure_not_upcoming(s)
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)

    if not (is_admin(me) or is_team_lead(me)):
        raise HTTPException(403, "Only a Team Lead or Admin may assign sub-tasks")
    await ensure_lead_edit_plan(db, me, plan)
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")

    parent = (
        await db.execute(
            select(Task).where(Task.id == s.task_id, Task.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not parent:
        raise HTTPException(404, "Parent task not found")

    if parent.recurring_group_id:
        series_filter = Task.recurring_group_id == parent.recurring_group_id
    elif parent.source_template_id:
        series_filter = Task.source_template_id == parent.source_template_id
    else:
        raise HTTPException(400, "This task is not part of a recurring series")

    member_ids = set(payload.user_ids)
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

    sib_rows = await db.execute(
        select(Task)
        .where(
            Task.tenant_id == me.tenant_id,
            Task.plan_id == parent.plan_id,
            Task.entity_id == parent.entity_id,
            series_filter,
        )
        .options(selectinload(Task.sub_tasks).selectinload(SubTask.entity_assignees))
    )
    sibling_tasks = list(sib_rows.scalars().all())

    # The SAME sub-task on each sibling occurrence: matched by sub-task
    # template when there is one, else by title (a custom recurring
    # task's sub-tasks have no template row, but the same title text is
    # copied onto every occurrence at materialization time).
    key = s.source_template_id or f"title:{s.title}"

    def _match(sub: SubTask) -> bool:
        return (sub.source_template_id or f"title:{sub.title}") == key

    siblings: list[SubTask] = []
    for t in sibling_tasks:
        for sub in t.sub_tasks:
            if _match(sub):
                siblings.append(sub)
                break

    if len(siblings) <= 1:
        raise HTTPException(
            400,
            "This sub-task is not part of a recurring series (only one "
            "occurrence in this plan) — assign it directly instead.",
        )

    updated = 0
    skipped = 0
    for sub in siblings:
        if sub.status in (TaskStatus.COMPLETED, TaskStatus.PENDING_REVIEW):
            skipped += 1
            continue
        if len(member_ids) >= 2:
            await _apply_subtask_multi_assignment(
                db, me, sub, plan, member_ids,
                log_action_name="SUBTASK_SERIES_ASSIGNED",
                log_meta={"member_count": len(member_ids), "series": True},
                notify=False,
            )
        else:
            # 0 ids -> None (clears this occurrence); 1 id -> that member.
            await _assign_single_member_to_subtask(
                db, me, sub, next(iter(member_ids), None), notify=False
            )
        await rollup_from_subtask(db, sub)
        updated += 1

    # One notification per assignee for the whole batch, not one per row.
    if updated:
        for uid in member_ids:
            if uid != me.id:
                await notify_user_subtask_assigned(db, me.tenant_id, uid, s.id, s.title)

    await db.commit()

    fresh = await _load_subtask(db, me.tenant_id, subtask_id)
    return SubTaskSeriesAssignOut(
        updated=updated,
        skipped=skipped,
        subtask=await _build_out(db, fresh),
    )


@router.post("/{subtask_id}/mark-done-entity", response_model=SubTaskOut)
async def mark_subtask_done_entity(
    subtask_id: str,
    payload: SubTaskEntityDoneIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """One assignee marks their own part of a multi-assignee sub-task done.
    Once every SubTaskEntityAssignee row is done the sub-task auto-submits
    for the Team Lead's review (mirrors mark-done-entity for tasks)."""
    s = await _load_subtask(db, me.tenant_id, subtask_id)
    _ensure_not_upcoming(s)
    plan = await _load_plan_for_subtask(db, me.tenant_id, s)

    if not s.assigned_entity_wide:
        raise HTTPException(400, "This sub-task is not assigned to multiple people")
    if plan.status == PlanStatus.CLOSED:
        raise HTTPException(400, "Plan is closed")
    if s.status == TaskStatus.COMPLETED:
        raise HTTPException(400, "Sub-task is already completed.")

    mine = next((a for a in s.entity_assignees if a.user_id == me.id), None)
    if not mine:
        raise HTTPException(403, "You are not assigned to this sub-task")

    if not mine.done:
        mine.done = True
        mine.done_at = datetime.now(timezone.utc)
        mine.completion_notes = payload.completion_notes
        mine.completion_time_minutes = payload.completion_time_minutes
        await record_completion_time_entry(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            entity_id=s.entity_id,
            period_id=plan.period_id,
            minutes=payload.completion_time_minutes,
            subtask_id=s.id,
        )
        await log_action(
            db,
            tenant_id=me.tenant_id,
            user_id=me.id,
            action="SUBTASK_ENTITY_MEMBER_DONE",
            entity_type="SubTask",
            entity_id=s.id,
        )

        if s.status in (
            TaskStatus.NOT_STARTED,
            TaskStatus.ASSIGNED,
            TaskStatus.REJECTED,
        ):
            s.status = TaskStatus.IN_PROGRESS

        if all(a.done for a in s.entity_assignees) and s.status != TaskStatus.PENDING_REVIEW:
            s.rejection_note = None
            s.status = TaskStatus.PENDING_REVIEW
            s.submitted_by_id = me.id
            s.submitted_at = datetime.now(timezone.utc)
            await log_action(
                db,
                tenant_id=me.tenant_id,
                user_id=me.id,
                action="SUBTASK_SUBMITTED",
                entity_type="SubTask",
                entity_id=s.id,
                meta={"via": "entity-all-done"},
            )
            reviewer_id = await _resolve_subtask_reviewer(db, me.tenant_id, s, plan)
            if reviewer_id and reviewer_id != me.id:
                await notify_user_subtask_submitted_for_review(
                    db, me.tenant_id, reviewer_id, s.id, s.title
                )

        await rollup_from_subtask(db, s)
        await db.commit()
        s = await _load_subtask(db, me.tenant_id, subtask_id)

    return await _build_out(db, s)
