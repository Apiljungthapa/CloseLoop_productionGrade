"""Periods + period configuration."""

import csv
import io

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..services.attachment_storage import read_upload_capped
from datetime import date, datetime
from ..models import (
    Period,
    Plan,
    Task,
    SubTask,
    User,
    Role,
    PeriodCadence,
    PeriodStatus,
    TaskStatus,
)
from ..schemas import (
    PeriodOut,
    PeriodConfigIn,
    PeriodConfigOut,
    PeriodGenerateIn,
    PeriodPreviewIn,
    PeriodPreviewOut,
    PeriodCloseOut,
    UserMini,
)
from ..auth import require_tenant, require_roles
from ..calendar import build_ics_bytes, known_holidays, nepal_today
from ..services.optional_tasks import is_skipped, task_is_skipped
from ..services.period_service import (
    get_or_create_config,
    ensure_initial_period,
    apply_period_schedule,
    generate_period_schedule,
    preview_period_schedule,
    bs_labels_for,
)
from ..schemas import PeriodSeedIn
from ..services.period_service import _latest_period
from ..services.carry_forward_service import close_period
from ..services.audit_service import log_action

router = APIRouter(prefix="/api/periods", tags=["periods"])


def _read_workbook_rows(content: bytes) -> list[list]:
    """Parse an .xlsx/.xlsm byte blob into a list of row lists. openpyxl is
    pure-Python and CPU-bound, so callers run this in a worker thread to
    keep the event loop responsive."""
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _period_out(p: Period) -> PeriodOut:
    """PeriodOut plus its derived BS start/end labels — the one place every
    endpoint below should build a period response from, so the BS Dates
    column and calendar widget never see a row without them."""
    out = PeriodOut.model_validate(p)
    for key, value in bs_labels_for(p.start_date, p.end_date).items():
        setattr(out, key, value)
    return out


# --------------------------------------------------------------------------
# Period-schedule file parsing (Excel / CSV) — feeds /import-file below.
# --------------------------------------------------------------------------


def _parse_period_date(value) -> date:
    """Coerce a spreadsheet cell into a `date`. Accepts native Excel
    datetimes (openpyxl hands these back already-typed for date-formatted
    cells) as well as common string formats."""
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        # datetime.date (and anything date-like) that isn't a datetime
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date '{value}'")


def _match_column(headers: list[str], *keywords: str) -> int | None:
    for i, h in enumerate(headers):
        low = (h or "").strip().lower()
        if any(k in low for k in keywords):
            return i
    return None


def _parse_period_rows(rows: list[list]) -> tuple[list[PeriodSeedIn], list[str]]:
    """Turn raw spreadsheet rows into PeriodSeedIn entries.

    Expects a LABEL, START_DATE, END_DATE shape. A header row is detected
    by name (columns containing "label"/"period", "start", "end"); if no
    header is recognized, the first three columns are used positionally —
    mirrors the paste-text importer's LABEL,START_DATE,END_DATE format.
    """
    seeds: list[PeriodSeedIn] = []
    errors: list[str] = []
    rows = [r for r in rows if r is not None]
    if not rows:
        return seeds, ["File has no rows"]

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    label_idx = _match_column(header, "label", "period")
    start_idx = _match_column(header, "start")
    end_idx = _match_column(header, "end")
    has_header = None not in (label_idx, start_idx, end_idx)

    data_rows = rows[1:] if has_header else rows
    row_offset = 2 if has_header else 1
    if not has_header:
        label_idx, start_idx, end_idx = 0, 1, 2

    for i, row in enumerate(data_rows, start=row_offset):
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        try:
            if max(label_idx, start_idx, end_idx) >= len(row):
                errors.append(f"Row {i}: expected at least 3 columns")
                continue
            label = str(row[label_idx]).strip() if row[label_idx] is not None else ""
            if not label:
                errors.append(f"Row {i}: missing label")
                continue
            if row[start_idx] is None or str(row[start_idx]).strip() == "":
                errors.append(f"Row {i}: missing start date")
                continue
            if row[end_idx] is None or str(row[end_idx]).strip() == "":
                errors.append(f"Row {i}: missing end date")
                continue
            start = _parse_period_date(row[start_idx])
            end = _parse_period_date(row[end_idx])
            if start > end:
                errors.append(f"Row {i}: start date must be on or before end date")
                continue
            seeds.append(PeriodSeedIn(label=label, start_date=start, end_date=end))
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
    return seeds, errors


# ---- config ----
@router.get("/configuration", response_model=PeriodConfigOut)
async def get_config(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    cfg = await get_or_create_config(db, me.tenant_id)
    await db.commit()
    return PeriodConfigOut.model_validate(cfg)


@router.put(
    "/configuration",
    response_model=PeriodConfigOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def update_config(
    payload: PeriodConfigIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    cfg = await get_or_create_config(db, me.tenant_id)
    cfg.cadence = payload.cadence
    cfg.start_day_of_week = payload.start_day_of_week
    cfg.duration_weeks = payload.duration_weeks
    cfg.auto_generate = payload.auto_generate
    cfg.carry_forward_enabled = payload.carry_forward_enabled
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PERIOD_CONFIG_UPDATED",
        entity_type="PeriodConfiguration",
        entity_id=cfg.id,
        meta={
            "cadence": cfg.cadence.value,
            "start_day_of_week": cfg.start_day_of_week,
            "duration_weeks": cfg.duration_weeks,
        },
    )
    await db.commit()
    await db.refresh(cfg)
    return PeriodConfigOut.model_validate(cfg)


# ---- periods ----
@router.get("", response_model=list[PeriodOut])
async def list_periods(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    rows = await db.execute(
        select(Period)
        .where(Period.tenant_id == me.tenant_id)
        .order_by(Period.start_date)
    )
    return [_period_out(p) for p in rows.scalars().all()]


@router.post(
    "/initialize",
    response_model=PeriodOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def initialize_first_period(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    p = await ensure_initial_period(db, me.tenant_id)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PERIOD_INITIALIZED",
        entity_type="Period",
        entity_id=p.id,
    )
    await db.commit()
    await db.refresh(p)
    return _period_out(p)


@router.get("/current", response_model=PeriodOut | None)
async def get_current(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    from ..services.period_service import get_current_period

    p = await get_current_period(db, me.tenant_id)
    return _period_out(p) if p else None


@router.get("/active", response_model=PeriodOut | None)
async def get_active(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    """Return the period whose date range covers today, regardless of status.
    Useful for the Retail Calendar where the active period is determined
    by today's date rather than by manual OPEN/CLOSED flipping."""
    from ..services.period_service import get_active_period_by_date

    p = await get_active_period_by_date(db, me.tenant_id)
    return _period_out(p) if p else None


@router.post(
    "/generate",
    response_model=list[PeriodOut],
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def generate_schedule(
    payload: PeriodGenerateIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a whole period schedule for ANY cadence — fixed-week (the
    original "P001..." fiscal pattern), calendar month/quarter/half-year,
    or the Bikram Sambat (Nepali) month/quarter/half-year of the fiscal
    year containing `start_date` — see PeriodCadence. The universal,
    reusable alternative to hand-pasting rows or the one-off 2026
    retail-calendar seed below: works for any tenant, not just a
    fixed-week client. Replaces the tenant's existing periods, same as
    /periods/import. Use POST /periods/preview first to see what this
    would create without committing it.
    """
    periods = await generate_period_schedule(
        db,
        me.tenant_id,
        payload.cadence,
        payload.start_date,
        payload.period_count,
        payload.duration_weeks,
        payload.start_day_of_week,
    )
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PERIOD_SCHEDULE_GENERATED",
        entity_type="Period",
        entity_id=periods[0].id if periods else None,
        meta={
            "cadence": payload.cadence.value,
            "start_date": payload.start_date.isoformat(),
            "count": len(periods),
        },
    )
    await db.commit()
    return [_period_out(p) for p in periods]


@router.post(
    "/preview",
    response_model=PeriodPreviewOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def preview_schedule(
    payload: PeriodPreviewIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run of POST /periods/generate — same cadence/start_date/count,
    but nothing is written to the database. Returns each period's label,
    BS + AD range, working-day count, Nepal-calendar closing date (last
    working day) and any public holidays inside it, so the admin can see
    exactly what will be created before committing to it."""
    dow = payload.start_day_of_week
    if dow is None and payload.cadence == PeriodCadence.FIXED_WEEKS:
        cfg = await get_or_create_config(db, me.tenant_id)
        dow = cfg.start_day_of_week
    seeds = preview_period_schedule(
        payload.cadence,
        payload.start_date,
        payload.period_count,
        payload.duration_weeks,
        dow,
    )
    return PeriodPreviewOut(cadence=payload.cadence, periods=seeds)


@router.get(
    "/{period_id}/review",
    response_model=dict,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def review_period(
    period_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Return completion metrics + incomplete tasks/subtasks for a period
    BEFORE closing it. Powers the "Review & Carry-Forward" dashboard so
    admins can see exactly what would be carried forward."""
    p = (
        await db.execute(
            select(Period).where(
                Period.id == period_id, Period.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Period not found")

    # Plans in this period
    plan_rows = await db.execute(
        select(Plan).where(Plan.tenant_id == me.tenant_id, Plan.period_id == period_id)
    )
    plans = list(plan_rows.scalars().all())
    plan_ids = [pl.id for pl in plans]

    if not plan_ids:
        return {
            "period": _period_out(p).model_dump(mode="json"),
            "totals": {
                "plans": 0,
                "tasks": 0,
                "tasks_completed": 0,
                "subtasks": 0,
                "subtasks_completed": 0,
            },
            "completion_pct": 0.0,
            "incomplete_tasks": [],
            "incomplete_subtasks": [],
        }

    task_rows = await db.execute(
        select(Task).where(Task.tenant_id == me.tenant_id, Task.plan_id.in_(plan_ids))
    )
    tasks = list(task_rows.scalars().all())
    task_ids = [t.id for t in tasks]
    sub_rows = await db.execute(
        select(SubTask).where(
            SubTask.tenant_id == me.tenant_id,
            SubTask.task_id.in_(task_ids) if task_ids else SubTask.id.is_(None),
        )
    )
    subs = list(sub_rows.scalars().all()) if task_ids else []

    # Collect user ids for enrichment
    user_ids: set[str] = set()
    for t in tasks:
        if t.assigned_to_id:
            user_ids.add(t.assigned_to_id)
    for s in subs:
        if s.assigned_to_id:
            user_ids.add(s.assigned_to_id)
        if s.reviewer_id:
            user_ids.add(s.reviewer_id)
    users_map: dict[str, UserMini] = {}
    if user_ids:
        urows = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {
            u.id: UserMini(id=u.id, name=u.name, title=u.title)
            for u in urows.scalars().all()
        }

    INCOMPLETE = (
        TaskStatus.NOT_STARTED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.PENDING_REVIEW,
        TaskStatus.REJECTED,
    )
    # Skipped work (optional, past due, not done) — tasks and the sub-tasks under
    # them — stays in the lists below but is never added to the totals or the
    # completion %. Every other optional task counts like a required one.
    today = nepal_today()
    parent_required = {t.id: t.is_required for t in tasks}
    counted_tasks = [t for t in tasks if not task_is_skipped(t, today)]
    counted_subs = [
        s for s in subs
        if not is_skipped(
            is_required=parent_required.get(s.task_id), status=s.status,
            due_date=s.due_date, today=today,
        )
    ]
    tasks_completed = sum(1 for t in counted_tasks if t.status == TaskStatus.COMPLETED)
    subs_completed = sum(1 for s in counted_subs if s.status == TaskStatus.COMPLETED)

    # subtasks whose parent task is COMPLETED can still be incomplete in edge cases;
    # we keep the simple rule: any task/subtask not COMPLETED counts as incomplete.
    incomplete_tasks = []
    sub_parent_task_ids = {s.task_id for s in subs if s.status in INCOMPLETE}
    for t in tasks:
        if t.status not in INCOMPLETE:
            continue
        # Skip tasks whose only "openness" comes from subtasks — those will be
        # represented in incomplete_subtasks below to avoid duplicates.
        if t.id in sub_parent_task_ids:
            continue
        incomplete_tasks.append(
            {
                "id": t.id,
                "plan_id": t.plan_id,
                "entity_id": t.entity_id,
                "title": t.title,
                "status": t.status.value,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "assigned_to": users_map.get(t.assigned_to_id).model_dump()
                if t.assigned_to_id and users_map.get(t.assigned_to_id)
                else None,
            }
        )

    incomplete_subtasks = []
    for s in subs:
        if s.status not in INCOMPLETE:
            continue
        incomplete_subtasks.append(
            {
                "id": s.id,
                "task_id": s.task_id,
                "entity_id": s.entity_id,
                "title": s.title,
                "status": s.status.value,
                "due_date": s.due_date.isoformat() if s.due_date else None,
                "assigned_to": users_map.get(s.assigned_to_id).model_dump()
                if s.assigned_to_id and users_map.get(s.assigned_to_id)
                else None,
                "reviewer": users_map.get(s.reviewer_id).model_dump()
                if s.reviewer_id and users_map.get(s.reviewer_id)
                else None,
            }
        )

    total = len(counted_tasks) + len(counted_subs)
    done = tasks_completed + subs_completed
    pct = (done / total) if total else 0.0

    return {
        "period": _period_out(p).model_dump(mode="json"),
        "totals": {
            "plans": len(plans),
            "tasks": len(counted_tasks),
            "tasks_completed": tasks_completed,
            "subtasks": len(counted_subs),
            "subtasks_completed": subs_completed,
        },
        "completion_pct": round(pct, 4),
        "incomplete_tasks": incomplete_tasks,
        "incomplete_subtasks": incomplete_subtasks,
    }


@router.post(
    "/{period_id}/close",
    response_model=PeriodCloseOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def close(
    period_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    p = (
        await db.execute(
            select(Period).where(
                Period.id == period_id, Period.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Period not found")
    try:
        closed, nxt, plans_created, cf_items = await close_period(
            db, me.tenant_id, p, me.id
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    await db.refresh(closed)
    await db.refresh(nxt)
    return PeriodCloseOut(
        closed_period=_period_out(closed),
        next_period=_period_out(nxt),
        plans_generated=plans_created,
        carry_forward_items=cf_items,
    )


@router.post(
    "/import",
    response_model=list[PeriodOut],
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def import_periods(
    payload: list[PeriodSeedIn],
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    if not payload:
        raise HTTPException(400, "No periods provided")
    created = await apply_period_schedule(db, me.tenant_id, payload)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PERIODS_IMPORTED",
        entity_type="Period",
        entity_id=created[0].id if created else None,
        meta={"count": len(created), "source": "paste"},
    )
    await db.commit()
    return [_period_out(p) for p in created]


@router.post(
    "/import-file",
    response_model=list[PeriodOut],
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def import_periods_file(
    file: UploadFile = File(...),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk-import a tenant's period schedule from an uploaded Excel (.xlsx/.xlsm)
    or CSV file — same replace-all semantics as POST /periods/import, just
    sourced from a spreadsheet instead of pasted text.

    Expected columns: Label, Start Date, End Date (any order; a header row
    is auto-detected by name, otherwise the first three columns are used
    positionally). Tenant-scoped like every other period operation here —
    `me.tenant_id` (from `require_tenant`) is the only tenant this can ever
    touch, so one client's uploaded schedule can never affect another's.
    """
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
            rows = await run_in_threadpool(_read_workbook_rows, content)
        except Exception as e:
            raise HTTPException(400, f"Could not read Excel file: {e}")
    else:
        raise HTTPException(
            400,
            "Unsupported file type — upload a .xlsx, .xlsm, or .csv file",
        )

    seeds, errors = _parse_period_rows(rows)
    if errors:
        raise HTTPException(
            400,
            f"Could not parse {len(errors)} row(s): " + "; ".join(errors[:10])
            + (" …" if len(errors) > 10 else ""),
        )
    if not seeds:
        raise HTTPException(400, "No valid period rows found in file")

    created = await apply_period_schedule(db, me.tenant_id, seeds)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="PERIODS_IMPORTED",
        entity_type="Period",
        entity_id=created[0].id if created else None,
        meta={"count": len(created), "source": "file", "filename": file.filename},
    )
    await db.commit()
    return [_period_out(p) for p in created]


@router.post(
    "/admin/trigger-scheduler",
    response_model=dict,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def trigger_scheduler_check(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    """
    Admin endpoint to manually trigger the background scheduler's period closure check.
    Useful for testing and immediate closure without waiting for the scheduled job.
    """
    from ..services.scheduler_service import _scheduler_job

    try:
        await _scheduler_job()
        return {
            "status": "success",
            "message": "Scheduler job executed. Check logs for details.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/export.ics")
async def export_calendar(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    """Download this tenant's period schedule as an .ics calendar feed —
    one all-day event per period (start date) plus every Nepal public
    holiday inside the span the periods cover, so admins can subscribe to
    close deadlines from Outlook/Google Calendar. Read-only, any tenant
    member may export it (matches GET /periods)."""
    rows = await db.execute(
        select(Period)
        .where(Period.tenant_id == me.tenant_id)
        .order_by(Period.start_date)
    )
    periods = list(rows.scalars().all())
    if not periods:
        raise HTTPException(400, "No periods to export yet")

    events: list[tuple[date, str]] = [
        (p.start_date, f"Period opens: {p.label}") for p in periods
    ] + [(p.end_date, f"Period closes: {p.label}") for p in periods]

    years = sorted({p.start_date.year for p in periods} | {p.end_date.year for p in periods})
    span_start, span_end = periods[0].start_date, periods[-1].end_date
    for d, n in known_holidays(range(years[0], years[-1] + 1)).items():
        if span_start <= d <= span_end:
            events.append((d, f"Holiday: {n}"))

    ics_bytes = build_ics_bytes(events, calendar_name=f"{me.tenant_id} — CloseLoop Periods")
    return Response(
        content=ics_bytes,
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=closeloop-periods.ics"},
    )
