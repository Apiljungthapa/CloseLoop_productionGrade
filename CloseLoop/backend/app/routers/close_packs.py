"""Close packs — reusable templates + versioning + a current-period report."""

import csv
import io
import logging

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..models import (
    ClosePack,
    Entity,
    Plan,
    Task,
    SubTask,
    TaskStatus,
    TaskTemplate,
    SubTaskTemplate,
    User,
    Role,
)
from ..schemas import (
    ClosePackIn,
    ClosePackUpdate,
    ClosePackOut,
    ClosePackTreeOut,
    TaskTemplateIn,
    TaskTemplateOut,
    SubTaskTemplateIn,
    SubTaskTemplateOut,
    PackCurrentPeriodReport,
    PackReportTaskRow,
    PackReportSubTaskRow,
)
from ..auth import require_tenant, require_roles
from ..services.attachment_storage import read_upload_capped
from ..services.close_pack_service import load_task_templates, import_task_templates
from ..services.audit_service import log_action
from ..calendar import nepal_today
from ..services.period_service import bs_labels_for, get_active_period_by_date, get_current_period
from ..services.plan_generation_service import sync_pack_to_open_plans
from ..services.due_date_rules import resolve_generated_due_date
from ..services.optional_tasks import is_skipped, task_is_skipped
from ..websocket import manager

router = APIRouter(prefix="/api/close-packs", tags=["close-packs"])
logger = logging.getLogger(__name__)


async def _sync_open_plans(
    db: AsyncSession,
    tenant_id: str,
    pack_id: str,
    user_id: str,
    *,
    orphan_task_ids: set[str] | None = None,
    orphan_subtask_ids: set[str] | None = None,
) -> None:
    """Propagate a just-made template change into every still-open plan
    generated from this pack. Best-effort: a sync failure is logged but
    never blocks the template edit itself (the change is still committed;
    the idempotent sync catches up on the next edit or a manual re-sync)."""
    pack = (
        await db.execute(
            select(ClosePack).where(
                ClosePack.id == pack_id, ClosePack.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if not pack:
        return
    try:
        result = await sync_pack_to_open_plans(
            db,
            tenant_id=tenant_id,
            pack=pack,
            user_id=user_id,
            orphan_task_ids=orphan_task_ids,
            orphan_subtask_ids=orphan_subtask_ids,
        )
    except Exception:  # noqa: BLE001 — never block a template edit
        logger.exception("Pack->plan sync failed for pack %s", pack_id)
        return
    if result.get("tasks_added") or result.get("tasks_removed") or result.get("tasks_updated"):
        try:
            await manager.broadcast_tenant(
                tenant_id, "plan:updated", {"source_pack_id": pack_id, **result}
            )
        except Exception:  # noqa: BLE001
            logger.exception("plan:updated broadcast failed for pack %s", pack_id)


async def _pack_task_ids_for_template(
    db: AsyncSession, tenant_id: str, template_id: str
) -> set[str]:
    rows = await db.execute(
        select(Task.id).where(
            Task.tenant_id == tenant_id, Task.source_template_id == template_id
        )
    )
    return {r[0] for r in rows.all()}


async def _pack_subtask_ids_for_template(
    db: AsyncSession, tenant_id: str, sub_template_id: str
) -> set[str]:
    rows = await db.execute(
        select(SubTask.id).where(
            SubTask.tenant_id == tenant_id,
            SubTask.source_template_id == sub_template_id,
        )
    )
    return {r[0] for r in rows.all()}

_TASK_TEMPLATE_FIELDS = (
    "id", "close_pack_id", "title", "description", "due_day_of_period",
    "due_anchor", "due_date_type", "post_period_due_days", "due_days",
    "frequency", "interval_count", "days_of_week", "is_required",
    "recurrence_start", "recurrence_end", "reviewer_required", "tags",
    "priority", "order", "due_time",
)
_SUB_TASK_TEMPLATE_FIELDS = (
    "id", "task_template_id", "title", "description", "due_day_of_period",
    "due_anchor", "due_date_type", "post_period_due_days", "due_days",
    "frequency", "interval_count", "days_of_week", "recurrence_start",
    "recurrence_end", "order", "due_time",
)

# Statuses a Task/SubTask still needs work on.
_OPEN = (
    TaskStatus.NOT_STARTED,
    TaskStatus.ASSIGNED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.REJECTED,
    TaskStatus.CARRIED_FORWARD,
)


async def _get_pack(db: AsyncSession, tenant_id: str, pack_id: str) -> ClosePack:
    p = (
        await db.execute(
            select(ClosePack).where(
                ClosePack.id == pack_id, ClosePack.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Not found")
    return p


# NOTE: there is no template-level "sub-task day <= task day" gate. A
# sub-task's day and anchor are its own; at plan generation the resolved
# sub-task due date is clamped to be on/before its parent task's
# (services/plan_generation_service._subtask_due), which is the only place
# the two are ever comparable across periods and anchors.


# ------- Close packs -------
@router.get("", response_model=list[ClosePackOut])
async def list_packs(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    rows = await db.execute(
        select(ClosePack)
        .where(ClosePack.tenant_id == me.tenant_id)
        .order_by(ClosePack.created_at.desc())
    )
    return [ClosePackOut.model_validate(p) for p in rows.scalars().all()]


@router.post(
    "",
    response_model=ClosePackOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def create_pack(
    payload: ClosePackIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    p = ClosePack(
        tenant_id=me.tenant_id,
        name=payload.name,
        description=payload.description,
        calendar_type=payload.calendar_type,
        active=payload.active,
        current_version=0,
    )
    db.add(p)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="CLOSE_PACK_CREATED",
        entity_type="ClosePack",
        entity_id=p.id,
        meta={"name": p.name, "calendar_type": p.calendar_type.value},
    )
    await db.commit()
    await db.refresh(p)
    return ClosePackOut.model_validate(p)


@router.get("/{pack_id}", response_model=ClosePackTreeOut)
async def get_pack(
    pack_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    p = await _get_pack(db, me.tenant_id, pack_id)
    templates = await load_task_templates(db, me.tenant_id, pack_id)
    return ClosePackTreeOut(
        **ClosePackOut.model_validate(p).model_dump(),
        task_templates=[
            TaskTemplateOut(
                **{k: getattr(t, k) for k in _TASK_TEMPLATE_FIELDS},
                sub_task_templates=[
                    SubTaskTemplateOut(**{k: getattr(s, k) for k in _SUB_TASK_TEMPLATE_FIELDS})
                    for s in t.sub_task_templates
                ],
            )
            for t in templates
        ],
    )


@router.patch(
    "/{pack_id}",
    response_model=ClosePackOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def update_pack(
    pack_id: str,
    payload: ClosePackUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    p = await _get_pack(db, me.tenant_id, pack_id)
    if payload.name is not None:
        p.name = payload.name
    if payload.description is not None:
        p.description = payload.description
    if payload.calendar_type is not None:
        p.calendar_type = payload.calendar_type
    if payload.active is not None:
        p.active = payload.active
    await db.commit()
    await db.refresh(p)
    return ClosePackOut.model_validate(p)


@router.delete(
    "/{pack_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def delete_pack(
    pack_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Delete a Close Pack and everything it ever generated.

    `Plan.source_pack_id` is ON DELETE SET NULL (so a report can still
    resolve a plan's history after its source pack is gone in the normal
    case), which means the pack row alone was safe to delete but left
    every Plan — and, via cascade, every Task/SubTask under it — orphaned
    with a null source_pack_id instead of actually disappearing. Deleting
    the pack is meant to remove it and everything it produced, so the
    plans have to be deleted explicitly first; cascades (Plan -> Task ->
    SubTask/comments/attachments/entity-assignees) take care of the rest.
    """
    p = await _get_pack(db, me.tenant_id, pack_id)

    plans = (
        await db.execute(
            select(Plan).where(Plan.tenant_id == me.tenant_id, Plan.source_pack_id == pack_id)
        )
    ).scalars().all()
    for plan in plans:
        await db.delete(plan)

    await db.delete(p)
    await db.commit()


@router.post(
    "/{pack_id}/sync-plans",
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def sync_plans(
    pack_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Force-propagate this pack's current template tree into every plan
    already generated from it whose period is still open. Normally this
    runs automatically on every template edit; this endpoint re-runs it
    on demand. Returns how many plans/tasks were touched."""
    pack = await _get_pack(db, me.tenant_id, pack_id)
    from ..services.plan_generation_service import sync_pack_to_open_plans

    result = await sync_pack_to_open_plans(
        db, tenant_id=me.tenant_id, pack=pack, user_id=me.id
    )
    await db.commit()
    return result


# ------- Task templates -------
@router.post(
    "/{pack_id}/task-templates",
    response_model=TaskTemplateOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def add_task_template(
    pack_id: str,
    payload: TaskTemplateIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    await _get_pack(db, me.tenant_id, pack_id)
    t = TaskTemplate(
        tenant_id=me.tenant_id,
        close_pack_id=pack_id,
        title=payload.title,
        description=payload.description,
        due_day_of_period=payload.due_day_of_period,
        due_anchor=payload.due_anchor,
        due_date_type=payload.due_date_type,
        post_period_due_days=payload.post_period_due_days,
        due_days=payload.due_days,
        reviewer_required=payload.reviewer_required,
        order=payload.order,
        frequency=payload.frequency,
        interval_count=payload.interval_count,
        days_of_week=payload.days_of_week,
        is_required=payload.is_required,
        recurrence_start=payload.recurrence_start,
        recurrence_end=payload.recurrence_end,
        due_time=payload.due_time,
        tags=payload.tags,
        priority=payload.priority,
    )
    db.add(t)
    await db.flush()
    await _sync_open_plans(db, me.tenant_id, pack_id, me.id)
    await db.commit()
    await db.refresh(t)
    return TaskTemplateOut(
        **{k: getattr(t, k) for k in _TASK_TEMPLATE_FIELDS},
        sub_task_templates=[],
    )


def _read_task_template_workbook(content: bytes) -> list[list]:
    """Parse an .xlsx/.xlsm byte blob into row lists, reading the "Tasks &
    Subtasks" sheet the admin template downloads (falls back to the first
    sheet that isn't named "Instructions", then the first sheet outright).
    openpyxl is pure-Python and CPU-bound, so this runs in a worker thread —
    same pattern as the Periods file importer (routers/periods.py)."""
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    name = next(
        (n for n in wb.sheetnames if n.strip().lower() == "tasks & subtasks"),
        next((n for n in wb.sheetnames if n.strip().lower() != "instructions"), wb.sheetnames[0]),
    )
    ws = wb[name]
    return [list(r) for r in ws.iter_rows(values_only=True)]


@router.post(
    "/{pack_id}/task-templates/import-file",
    response_model=ClosePackTreeOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def import_task_templates_file(
    pack_id: str,
    file: UploadFile = File(...),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-create tasks/subtasks in this pack from an uploaded .xlsx/.xlsm
    or .csv built from the "Download template" button. Additive — appends
    to whatever the pack already has, never deletes anything. All-or-
    nothing: if any row is invalid the whole upload is rejected with every
    row's error at once, so nothing is half-created."""
    await _get_pack(db, me.tenant_id, pack_id)

    filename = (file.filename or "").lower()
    try:
        content = await read_upload_capped(file, 5 * 1024 * 1024)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if filename.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    elif filename.endswith(".xlsx") or filename.endswith(".xlsm"):
        try:
            rows = await run_in_threadpool(_read_task_template_workbook, content)
        except Exception as e:
            raise HTTPException(400, f"Could not read Excel file: {e}")
    else:
        raise HTTPException(
            400, "Unsupported file type — upload a .xlsx, .xlsm, or .csv file"
        )

    try:
        result = await import_task_templates(db, me.tenant_id, pack_id, rows, me.id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="TASK_TEMPLATES_IMPORTED",
        entity_type="ClosePack",
        entity_id=pack_id,
        meta=result,
    )
    await _sync_open_plans(db, me.tenant_id, pack_id, me.id)
    await db.commit()

    p = await _get_pack(db, me.tenant_id, pack_id)
    templates = await load_task_templates(db, me.tenant_id, pack_id)
    return ClosePackTreeOut(
        **ClosePackOut.model_validate(p).model_dump(),
        task_templates=[
            TaskTemplateOut(
                **{k: getattr(t, k) for k in _TASK_TEMPLATE_FIELDS},
                sub_task_templates=[
                    SubTaskTemplateOut(**{k: getattr(s, k) for k in _SUB_TASK_TEMPLATE_FIELDS})
                    for s in t.sub_task_templates
                ],
            )
            for t in templates
        ],
    )


@router.patch(
    "/task-templates/{template_id}",
    response_model=TaskTemplateOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def update_task_template(
    template_id: str,
    payload: TaskTemplateIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    t = (
        await db.execute(
            select(TaskTemplate)
            .where(TaskTemplate.id == template_id, TaskTemplate.tenant_id == me.tenant_id)
            .options(selectinload(TaskTemplate.sub_task_templates))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Not found")
    for field in ("title", "description", "due_day_of_period", "due_anchor", "due_date_type", "post_period_due_days", "due_days", "frequency", "interval_count", "days_of_week", "is_required", "recurrence_start", "recurrence_end", "reviewer_required", "tags", "priority", "order", "due_time"):
        setattr(t, field, getattr(payload, field))
    await db.flush()
    await _sync_open_plans(db, me.tenant_id, t.close_pack_id, me.id)
    await db.commit()
    await db.refresh(t)
    return TaskTemplateOut(
        **{k: getattr(t, k) for k in _TASK_TEMPLATE_FIELDS},
        sub_task_templates=[
            SubTaskTemplateOut(**{k: getattr(s, k) for k in _SUB_TASK_TEMPLATE_FIELDS})
            for s in t.sub_task_templates
        ],
    )


@router.delete(
    "/task-templates/{template_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def delete_task_template(
    template_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    t = (
        await db.execute(
            select(TaskTemplate).where(
                TaskTemplate.id == template_id, TaskTemplate.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Not found")
    pack_id = t.close_pack_id
    # Capture the materialized Task ids now — once the template row is gone
    # the FK is SET NULL and the link is lost.
    orphan_task_ids = await _pack_task_ids_for_template(db, me.tenant_id, template_id)
    await db.delete(t)
    await db.flush()
    await _sync_open_plans(
        db, me.tenant_id, pack_id, me.id, orphan_task_ids=orphan_task_ids
    )
    await db.commit()


async def _validate_sub_task_schedule(
    db: AsyncSession, tenant_id: str, payload: SubTaskTemplateIn, parent: TaskTemplate
) -> None:
    """A sub-task template always carries its own schedule now — it no
    longer falls back to the parent task's due date by leaving
    `due_day_of_period` blank (see SubTaskTemplate.frequency docstring) —
    but it must never resolve later than the parent task's own due date.

    Only checkable up front when NEITHER the sub-task nor its parent task
    recurs: both are then a single, period-relative "day N" that resolves
    to a real date the same way for every period. A recurring pairing is a
    moving target across periods (each occurrence's parent due date
    differs), so that case is left to the generation-time clamp instead
    (see plan_generation_service._subtask_due) rather than guessed at here.

    Uses `get_current_period` (the tenant's OPEN/IN_PROGRESS period) rather
    than "whichever period's date range covers today" — an admin routinely
    edits templates for the period that's about to run, or already
    running, days before/after its actual start/end date; anchoring to
    today's exact date would silently skip this check whenever today falls
    in a gap between periods.
    """
    if payload.frequency is None and payload.due_day_of_period is None:
        raise HTTPException(
            400,
            "Give this sub-task its own due day (or a frequency) — it no longer "
            "automatically falls back to the parent task's due date.",
        )
    if payload.frequency is not None or parent.frequency is not None:
        return
    period = await get_current_period(db, tenant_id)
    if not period:
        return
    parent_due = resolve_generated_due_date(
        period.start_date, period.end_date, parent.due_day_of_period, parent.due_anchor,
        parent.due_date_type, parent.post_period_due_days,
    )
    sub_due = resolve_generated_due_date(
        period.start_date, period.end_date, payload.due_day_of_period, payload.due_anchor,
        payload.due_date_type, payload.post_period_due_days,
    )
    if sub_due > parent_due:
        raise HTTPException(
            400,
            f"This sub-task would be due {sub_due.isoformat()} — after its parent task's "
            f"due date {parent_due.isoformat()}. Pick an earlier day (or a different anchor).",
        )


@router.post(
    "/task-templates/{template_id}/sub-task-templates",
    response_model=SubTaskTemplateOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def add_sub_task_template(
    template_id: str,
    payload: SubTaskTemplateIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    parent = (
        await db.execute(
            select(TaskTemplate).where(
                TaskTemplate.id == template_id, TaskTemplate.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not parent:
        raise HTTPException(404, "Task template not found")
    await _validate_sub_task_schedule(db, me.tenant_id, payload, parent)
    s = SubTaskTemplate(
        tenant_id=me.tenant_id,
        task_template_id=template_id,
        title=payload.title,
        description=payload.description,
        due_day_of_period=payload.due_day_of_period,
        due_anchor=payload.due_anchor,
        due_date_type=payload.due_date_type,
        post_period_due_days=payload.post_period_due_days,
        due_days=payload.due_days,
        order=payload.order,
        due_time=payload.due_time,
        frequency=payload.frequency,
        interval_count=payload.interval_count,
        days_of_week=payload.days_of_week,
        recurrence_start=payload.recurrence_start,
        recurrence_end=payload.recurrence_end,
    )
    db.add(s)
    await db.flush()
    await _sync_open_plans(db, me.tenant_id, parent.close_pack_id, me.id)
    await db.commit()
    await db.refresh(s)
    return SubTaskTemplateOut(**{k: getattr(s, k) for k in _SUB_TASK_TEMPLATE_FIELDS})


@router.patch(
    "/sub-task-templates/{template_id}",
    response_model=SubTaskTemplateOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def update_sub_task_template(
    template_id: str,
    payload: SubTaskTemplateIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = (
        await db.execute(
            select(SubTaskTemplate).where(
                SubTaskTemplate.id == template_id,
                SubTaskTemplate.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Not found")
    parent = (
        await db.execute(select(TaskTemplate).where(TaskTemplate.id == s.task_template_id))
    ).scalar_one_or_none()
    if parent:
        await _validate_sub_task_schedule(db, me.tenant_id, payload, parent)
    for field in ("title", "description", "due_day_of_period", "due_anchor", "due_date_type", "post_period_due_days", "due_days", "frequency", "interval_count", "days_of_week", "recurrence_start", "recurrence_end", "order", "due_time"):
        setattr(s, field, getattr(payload, field))
    await db.flush()
    if parent:
        await _sync_open_plans(db, me.tenant_id, parent.close_pack_id, me.id)
    await db.commit()
    await db.refresh(s)
    return SubTaskTemplateOut(**{k: getattr(s, k) for k in _SUB_TASK_TEMPLATE_FIELDS})


@router.delete(
    "/sub-task-templates/{template_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def delete_sub_task_template(
    template_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    s = (
        await db.execute(
            select(SubTaskTemplate).where(
                SubTaskTemplate.id == template_id,
                SubTaskTemplate.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Not found")
    parent = (
        await db.execute(
            select(TaskTemplate).where(TaskTemplate.id == s.task_template_id)
        )
    ).scalar_one_or_none()
    orphan_subtask_ids = await _pack_subtask_ids_for_template(
        db, me.tenant_id, template_id
    )
    await db.delete(s)
    await db.flush()
    if parent:
        await _sync_open_plans(
            db,
            me.tenant_id,
            parent.close_pack_id,
            me.id,
            orphan_subtask_ids=orphan_subtask_ids,
        )
    await db.commit()


# ------- Current-period report -------
@router.get("/{pack_id}/current-period-report", response_model=PackCurrentPeriodReport)
async def current_period_report(
    pack_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """The FULL state of every task/subtask this pack generated for the
    period currently running (the tenant period whose date range covers
    today) — status, due date, assignee, completion notes — so Reports can
    show exactly what has and hasn't been done."""
    pack = await _get_pack(db, me.tenant_id, pack_id)
    today = nepal_today()

    period = await get_active_period_by_date(db, me.tenant_id)
    empty_totals = {
        "total": 0, "completed": 0, "pending": 0, "in_progress": 0,
        "pending_review": 0, "rejected": 0, "carried_forward": 0, "missed": 0,
        "assigned": 0, "unassigned": 0,
        "subtasks_total": 0, "subtasks_completed": 0,
    }
    if not period:
        return PackCurrentPeriodReport(
            pack_id=pack_id, pack_name=pack.name, calendar_type=pack.calendar_type,
            period=None, totals=empty_totals, completion_pct=0.0, tasks=[],
        )

    plan_rows = await db.execute(
        select(Plan.id).where(
            Plan.tenant_id == me.tenant_id,
            Plan.source_pack_id == pack_id,
            Plan.period_id == period.id,
        )
    )
    plan_ids = [r[0] for r in plan_rows.all()]

    tasks: list[Task] = []
    if plan_ids:
        rows = await db.execute(
            select(Task)
            .where(Task.tenant_id == me.tenant_id, Task.plan_id.in_(plan_ids))
            .options(selectinload(Task.sub_tasks))
            .order_by(Task.created_at, Task.title)
        )
        tasks = list(rows.scalars().all())

    ent_map = {
        eid: name
        for eid, name in (
            await db.execute(
                select(Entity.id, Entity.name).where(Entity.tenant_id == me.tenant_id)
            )
        ).all()
    }
    user_ids = {t.assigned_to_id or t.member_assigned_to_id for t in tasks} - {None}
    user_map = {
        uid: name
        for uid, name in (
            await db.execute(select(User.id, User.name).where(User.id.in_(user_ids or ["_"])))
        ).all()
    }

    totals = dict(empty_totals)
    out_rows: list[PackReportTaskRow] = []

    def _assigned(o) -> bool:
        return bool(
            getattr(o, "assigned_to_id", None)
            or getattr(o, "member_assigned_to_id", None)
            or getattr(o, "assigned_entity_wide", False)
        )

    for t in tasks:
        # A skipped task (optional, past due, not done) keeps its row but is
        # never added to the totals — its increments land on a throw-away copy.
        counted = dict(totals) if task_is_skipped(t, today) else totals
        counted["total"] += 1
        if t.status == TaskStatus.COMPLETED:
            counted["completed"] += 1
        elif t.status == TaskStatus.PENDING_REVIEW:
            counted["pending_review"] += 1
        elif t.status == TaskStatus.REJECTED:
            counted["rejected"] += 1
        elif t.status in (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED):
            counted["in_progress"] += 1
        elif t.status == TaskStatus.CARRIED_FORWARD:
            counted["carried_forward"] += 1
        else:
            counted["pending"] += 1
        if _assigned(t):
            counted["assigned"] += 1
        else:
            counted["unassigned"] += 1
        if t.status != TaskStatus.COMPLETED and t.due_date and t.due_date < today:
            counted["missed"] += 1

        subs: list[PackReportSubTaskRow] = []
        for s in sorted(t.sub_tasks, key=lambda x: (x.created_at, x.title)):
            # A sub-task inherits its parent's optional flag.
            sub_counted = dict(counted) if is_skipped(
                is_required=t.is_required, status=s.status,
                due_date=s.due_date, today=today,
            ) else counted
            sub_counted["subtasks_total"] += 1
            if s.status == TaskStatus.COMPLETED:
                sub_counted["subtasks_completed"] += 1
            subs.append(PackReportSubTaskRow(
                id=s.id, title=s.title, status=s.status, due_date=s.due_date,
                assigned=_assigned(s), completion_notes=s.completion_notes,
            ))

        out_rows.append(PackReportTaskRow(
            id=t.id, title=t.title, entity_id=t.entity_id,
            entity_name=ent_map.get(t.entity_id),
            status=t.status, due_date=t.due_date, assigned=_assigned(t),
            assignee_name=user_map.get(t.assigned_to_id or t.member_assigned_to_id),
            completion_notes=t.completion_notes, subtasks=subs,
        ))

    return PackCurrentPeriodReport(
        pack_id=pack_id,
        pack_name=pack.name,
        calendar_type=pack.calendar_type,
        period={
            "id": period.id,
            "label": period.label,
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            **bs_labels_for(period.start_date, period.end_date),
        },
        totals=totals,
        completion_pct=round(totals["completed"] / totals["total"], 4) if totals["total"] else 0.0,
        tasks=out_rows,
    )
