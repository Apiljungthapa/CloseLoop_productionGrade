"""
Report Engine HTTP layer.

Five Phase-1 reports — see Report Engine spec (Task 3 §2):

    GET /api/reports/period-close-completion?period_id=...&group_id=...&entity_id=...
    GET /api/reports/overdue-tasks?period_id=...
    GET /api/reports/user-workload?period_id=...&target_user_id=...
    GET /api/reports/year-end-rollup?entity_id=...
    GET /api/reports/time-entries?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&...

Each endpoint accepts ``?format=json|csv|pdf`` and is fully synchronous
(in-memory rendering — no Celery, no S3, no background worker — per
spec §3.1). Every call is gated by ``report_access_service`` for the
strict RBAC matrix (Team Members may only run their own
User Workload report; Group Managers / Team Leads are auto-scoped to
their accessible entities; Super Admins see the whole tenant).

A persistent companion endpoint exposes the immutable
``ReportSnapshot`` rows created either by the period-close event hook or
by a future on-demand "Save Snapshot" UI button:

    GET  /api/reports/snapshots
    GET  /api/reports/snapshots/{snapshot_id}
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import partial
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..auth import require_tenant
from ..database import get_db
from ..models import ReportSnapshot, Tenant, User
from ..schemas import ReportEnvelopeOut, ReportSnapshotOut, UserMini
from ..services import report_access_service as access
from ..services.pdf_service import (
    label_for as report_label_for,
    render_report_csv,
    render_report_pdf,
)
from ..services.report_service import (
    ReportResult,
    current_period_status_report,
    overdue_task_report,
    period_close_completion_report,
    time_entry_report,
    user_workload_report,
    year_end_rollup_report,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

_ALLOWED_FORMATS = {"json", "csv", "pdf"}


# ============================================================
# Shared helpers
# ============================================================
def _validate_format(fmt: str) -> str:
    f = (fmt or "json").lower()
    if f not in _ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{fmt}'. Use one of: json, csv, pdf.",
        )
    return f


async def _tenant_name(db: AsyncSession, tenant_id: str) -> Optional[str]:
    row = await db.execute(select(Tenant.name).where(Tenant.id == tenant_id))
    return row.scalar_one_or_none()


def _user_mini(user: User) -> UserMini:
    return UserMini(id=user.id, name=user.name, title=user.title)


def _ascii_safe(value: str) -> str:
    """Reduce an arbitrary string to an ASCII-safe filename token.

    HTTP header values must be Latin-1 encodable, so we strip any
    non-ASCII characters (Hindi, accented, emoji, etc.) and collapse
    runs of unsafe characters into single underscores. We additionally
    drop quotes, semicolons, slashes and other characters that confuse
    Content-Disposition parsers.
    """
    import re
    import unicodedata

    # 1) Decompose unicode then keep only ASCII bytes — preserves "naive"
    #    transliteration for accented Latin without crashing on CJK / Hindi.
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    # 2) Replace any character that is not alphanumeric / dash / underscore / dot
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("._-")
    return safe or "report"


def _csv_filename(report_type: str, *parts: str) -> str:
    bits = [report_type] + [p for p in parts if p]
    stem = "_".join(_ascii_safe(b) for b in bits if b)
    return f"{stem or 'report'}.csv"


def _pdf_filename(report_type: str, *parts: str) -> str:
    bits = [report_type] + [p for p in parts if p]
    stem = "_".join(_ascii_safe(b) for b in bits if b)
    return f"{stem or 'report'}.pdf"


def _content_disposition(filename: str) -> str:
    """Build a Content-Disposition value that is always Latin-1 safe."""
    # _csv_filename / _pdf_filename already strip to ASCII, but encode any
    # leftover quotes/back-slashes that would corrupt the header.
    safe = filename.replace('"', "").replace("\\", "")
    return f'attachment; filename="{safe}"'


async def _csv_response(result: ReportResult, filename: str) -> StreamingResponse:
    try:
        body = await run_in_threadpool(
            render_report_csv, result.columns, result.rows
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("CSV rendering failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"CSV rendering failed: {type(exc).__name__}: {exc}",
        ) from exc
    # Encode to UTF-8 bytes with a BOM so Excel opens the file as UTF-8.
    payload = ("\ufeff" + body).encode("utf-8")
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


async def _pdf_response(
    *,
    report_type: str,
    me: User,
    tenant_name: Optional[str],
    result: ReportResult,
    filename: str,
    period_label: Optional[str] = None,
    entity_name: Optional[str] = None,
    target_user_name: Optional[str] = None,
    parameters: Optional[dict[str, Any]] = None,
) -> Response:
    try:
        # ReportLab rendering is synchronous and CPU-bound — run it in a
        # worker thread so a large report doesn't stall the event loop.
        pdf_bytes = await run_in_threadpool(
            partial(
                render_report_pdf,
                report_type=report_type,
                report_type_label=report_label_for(report_type),
                tenant_name=tenant_name or "-",
                generated_by_name=me.name,
                generated_at=datetime.now(timezone.utc),
                columns=result.columns,
                rows=result.rows,
                period_label=period_label,
                entity_name=entity_name,
                target_user_name=target_user_name,
                summary=result.summary or None,
                parameters=parameters or None,
            )
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("PDF rendering failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"PDF rendering failed: {type(exc).__name__}: {exc}",
        ) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


def _json_envelope(
    *,
    report_type: str,
    me: User,
    tenant_id: str,
    tenant_name: Optional[str],
    result: ReportResult,
    period_id: Optional[str] = None,
    period_label: Optional[str] = None,
    entity_id: Optional[str] = None,
    entity_name: Optional[str] = None,
    parameters: Optional[dict[str, Any]] = None,
) -> ReportEnvelopeOut:
    return ReportEnvelopeOut(
        report_type=report_type,
        generated_at=datetime.now(timezone.utc),
        generated_by=_user_mini(me),
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        period_id=period_id,
        period_label=period_label,
        entity_id=entity_id,
        entity_name=entity_name,
        parameters=parameters or {},
        columns=result.columns,
        rows=result.rows,
        summary=result.summary,
    )


async def _period_label(
    db: AsyncSession, tenant_id: str, period_id: str
) -> Optional[str]:
    from ..models import Period

    row = await db.execute(
        select(Period.label).where(
            Period.tenant_id == tenant_id, Period.id == period_id
        )
    )
    return row.scalar_one_or_none()


async def _entity_label(
    db: AsyncSession, tenant_id: str, entity_id: str
) -> Optional[str]:
    from ..models import Entity

    row = await db.execute(
        select(Entity.name).where(Entity.tenant_id == tenant_id, Entity.id == entity_id)
    )
    return row.scalar_one_or_none()


async def _user_label(db: AsyncSession, tenant_id: str, user_id: str) -> Optional[str]:
    row = await db.execute(
        select(User.name).where(User.tenant_id == tenant_id, User.id == user_id)
    )
    return row.scalar_one_or_none()


# ============================================================
# 2.1 — Period Close Completion Report
# ============================================================
@router.get("/period-close-completion")
async def period_close_completion(
    period_id: str = Query(..., description="Target period id"),
    group_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    format: str = Query("json"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    access.ensure_can_run_report(me, access.REPORT_PERIOD_CLOSE_COMPLETION)
    fmt = _validate_format(format)
    tid = me.tenant_id or ""

    entity_scope = await access.resolve_report_entity_scope(db, me)
    if entity_id:
        await access.ensure_can_view_entity(db, me, entity_id)

    result = await period_close_completion_report(
        db,
        tenant_id=tid,
        period_id=period_id,
        entity_scope=entity_scope,
        group_id=group_id,
        entity_id=entity_id,
    )
    tenant_name = await _tenant_name(db, tid)
    period_label = await _period_label(db, tid, period_id)
    entity_name = await _entity_label(db, tid, entity_id) if entity_id else None
    parameters = {
        "period_id": period_id,
        "group_id": group_id,
        "entity_id": entity_id,
    }

    if fmt == "csv":
        return await _csv_response(
            result,
            _csv_filename("period_close_completion", period_label or period_id),
        )
    if fmt == "pdf":
        return await _pdf_response(
            report_type=access.REPORT_PERIOD_CLOSE_COMPLETION,
            me=me,
            tenant_name=tenant_name,
            result=result,
            filename=_pdf_filename(
                "period_close_completion", period_label or period_id
            ),
            period_label=period_label,
            entity_name=entity_name,
            parameters=parameters,
        )
    return _json_envelope(
        report_type=access.REPORT_PERIOD_CLOSE_COMPLETION,
        me=me,
        tenant_id=tid,
        tenant_name=tenant_name,
        result=result,
        period_id=period_id,
        period_label=period_label,
        entity_id=entity_id,
        entity_name=entity_name,
        parameters=parameters,
    )


# ============================================================
# Current Period Status Report
# ============================================================
@router.get("/current-period-status")
async def current_period_status(
    period_id: Optional[str] = Query(
        None,
        description="Defaults to the period covering today; pass an id to target another period.",
    ),
    group_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    format: str = Query("json"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    access.ensure_can_run_report(me, access.REPORT_CURRENT_PERIOD_STATUS)
    fmt = _validate_format(format)
    tid = me.tenant_id or ""

    entity_scope = await access.resolve_report_entity_scope(db, me)
    if entity_id:
        await access.ensure_can_view_entity(db, me, entity_id)

    result = await current_period_status_report(
        db,
        tenant_id=tid,
        entity_scope=entity_scope,
        period_id=period_id,
        group_id=group_id,
        entity_id=entity_id,
    )
    tenant_name = await _tenant_name(db, tid)
    resolved_period_id = result.summary.get("period_id")
    period_label = result.summary.get("period_label")
    entity_name = await _entity_label(db, tid, entity_id) if entity_id else None
    parameters = {
        "period_id": period_id,
        "resolved_period_id": resolved_period_id,
        "group_id": group_id,
        "entity_id": entity_id,
    }

    if fmt == "csv":
        return await _csv_response(
            result,
            _csv_filename("current_period_status", period_label or resolved_period_id or ""),
        )
    if fmt == "pdf":
        return await _pdf_response(
            report_type=access.REPORT_CURRENT_PERIOD_STATUS,
            me=me,
            tenant_name=tenant_name,
            result=result,
            filename=_pdf_filename(
                "current_period_status", period_label or resolved_period_id or ""
            ),
            period_label=period_label,
            entity_name=entity_name,
            parameters=parameters,
        )
    return _json_envelope(
        report_type=access.REPORT_CURRENT_PERIOD_STATUS,
        me=me,
        tenant_id=tid,
        tenant_name=tenant_name,
        result=result,
        period_id=resolved_period_id,
        period_label=period_label,
        entity_id=entity_id,
        entity_name=entity_name,
        parameters=parameters,
    )


# ============================================================
# 2.2 — Overdue Task Report
# ============================================================
@router.get("/overdue-tasks")
async def overdue_tasks(
    period_id: Optional[str] = Query(None),
    format: str = Query("json"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    access.ensure_can_run_report(me, access.REPORT_OVERDUE_TASK)
    fmt = _validate_format(format)
    tid = me.tenant_id or ""

    entity_scope = await access.resolve_report_entity_scope(db, me)
    result = await overdue_task_report(
        db,
        tenant_id=tid,
        entity_scope=entity_scope,
        period_id=period_id,
    )
    tenant_name = await _tenant_name(db, tid)
    period_label = await _period_label(db, tid, period_id) if period_id else None
    parameters = {"period_id": period_id}

    if fmt == "csv":
        return await _csv_response(result, _csv_filename("overdue_tasks", period_label or ""))
    if fmt == "pdf":
        return await _pdf_response(
            report_type=access.REPORT_OVERDUE_TASK,
            me=me,
            tenant_name=tenant_name,
            result=result,
            filename=_pdf_filename("overdue_tasks", period_label or ""),
            period_label=period_label,
            parameters=parameters,
        )
    return _json_envelope(
        report_type=access.REPORT_OVERDUE_TASK,
        me=me,
        tenant_id=tid,
        tenant_name=tenant_name,
        result=result,
        period_id=period_id,
        period_label=period_label,
        parameters=parameters,
    )


# ============================================================
# 2.3 — User Workload and Progress Report
# ============================================================
@router.get("/user-workload")
async def user_workload(
    period_id: str = Query(..., description="Target period id"),
    target_user_id: Optional[str] = Query(
        None,
        description="Defaults to the caller. Team Members may NOT pass a different value.",
    ),
    format: str = Query("json"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    access.ensure_can_run_report(me, access.REPORT_USER_WORKLOAD)
    fmt = _validate_format(format)
    tid = me.tenant_id or ""

    if target_user_id:
        # An explicit target — Team Members may only target themselves;
        # anyone else can target any user in their report scope.
        effective_target: Optional[str] = target_user_id
        if effective_target != me.id:
            await access.ensure_can_view_user(db, me, effective_target)
        result = await user_workload_report(
            db, tenant_id=tid, target_user_id=effective_target, period_id=period_id,
        )
        user_name = await _user_label(db, tid, effective_target)
    else:
        # No target passed — report on every user in the caller's scope
        # ("All Users" in the UI), not just the caller. A Team Lead or
        # Super Admin picking "All Users" expects to see their whole
        # team's workload, not their own (often empty) one.
        scope = await access.resolve_report_user_scope(db, me)
        if scope is None:
            rows = await db.execute(select(User.id).where(User.tenant_id == tid))
            target_ids = [r[0] for r in rows.all()]
        else:
            target_ids = sorted(scope)

        if len(target_ids) <= 1:
            effective_target = target_ids[0] if target_ids else me.id
            result = await user_workload_report(
                db, tenant_id=tid, target_user_id=effective_target, period_id=period_id,
            )
            user_name = await _user_label(db, tid, effective_target)
        else:
            effective_target = None
            user_name = "All Users"
            merged_rows: list[dict[str, Any]] = []
            columns: list[str] = []
            total_assigned = completed = completed_on_time = overdue_open = 0
            total_minutes = 0
            for uid in target_ids:
                r = await user_workload_report(
                    db, tenant_id=tid, target_user_id=uid, period_id=period_id,
                )
                columns = r.columns
                merged_rows.extend(r.rows)
                s = r.summary or {}
                total_assigned += s.get("total_assigned", 0)
                completed += s.get("completed", 0)
                completed_on_time += s.get("completed_on_time", 0)
                overdue_open += s.get("overdue_open", 0)
                total_minutes += s.get("total_timer_minutes", 0)
            open_items = total_assigned - completed
            overdue_rate = (overdue_open / open_items) if open_items else 0.0
            result = ReportResult(
                columns=columns,
                rows=merged_rows,
                summary={
                    "user_id": None,
                    "user_name": user_name,
                    "period_id": period_id,
                    "total_assigned": total_assigned,
                    "completed": completed,
                    "completed_on_time": completed_on_time,
                    "open": open_items,
                    "overdue_open": overdue_open,
                    "overdue_rate": round(overdue_rate, 4),
                    "total_timer_hours": round(total_minutes / 60.0, 2),
                    "total_timer_minutes": total_minutes,
                },
            )

    tenant_name = await _tenant_name(db, tid)
    period_label = await _period_label(db, tid, period_id)
    parameters = {"period_id": period_id, "target_user_id": effective_target}

    if fmt == "csv":
        return await _csv_response(
            result,
            _csv_filename(
                "user_workload",
                period_label or period_id,
                user_name or effective_target,
            ),
        )
    if fmt == "pdf":
        return await _pdf_response(
            report_type=access.REPORT_USER_WORKLOAD,
            me=me,
            tenant_name=tenant_name,
            result=result,
            filename=_pdf_filename(
                "user_workload",
                period_label or period_id,
                user_name or effective_target,
            ),
            period_label=period_label,
            target_user_name=user_name,
            parameters=parameters,
        )
    return _json_envelope(
        report_type=access.REPORT_USER_WORKLOAD,
        me=me,
        tenant_id=tid,
        tenant_name=tenant_name,
        result=result,
        period_id=period_id,
        period_label=period_label,
        parameters=parameters,
    )


# ============================================================
# 2.4 — Year-End Rollup Report
# ============================================================
@router.get("/year-end-rollup")
async def year_end_rollup(
    entity_id: str = Query(..., description="Target entity id"),
    format: str = Query("json"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    access.ensure_can_run_report(me, access.REPORT_YEAR_END_ROLLUP)
    fmt = _validate_format(format)
    tid = me.tenant_id or ""

    await access.ensure_can_view_entity(db, me, entity_id)

    result = await year_end_rollup_report(db, tenant_id=tid, entity_id=entity_id)
    tenant_name = await _tenant_name(db, tid)
    entity_name = await _entity_label(db, tid, entity_id)
    parameters = {"entity_id": entity_id}

    if fmt == "csv":
        return await _csv_response(
            result, _csv_filename("year_end_rollup", entity_name or entity_id)
        )
    if fmt == "pdf":
        return await _pdf_response(
            report_type=access.REPORT_YEAR_END_ROLLUP,
            me=me,
            tenant_name=tenant_name,
            result=result,
            filename=_pdf_filename("year_end_rollup", entity_name or entity_id),
            entity_name=entity_name,
            parameters=parameters,
        )
    return _json_envelope(
        report_type=access.REPORT_YEAR_END_ROLLUP,
        me=me,
        tenant_id=tid,
        tenant_name=tenant_name,
        result=result,
        entity_id=entity_id,
        entity_name=entity_name,
        parameters=parameters,
    )


# ============================================================
# 2.5 — Time Entry Report
# ============================================================
@router.get("/time-entries")
async def time_entries(
    start_date: date = Query(...),
    end_date: date = Query(...),
    entity_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    period_id: Optional[str] = Query(None),
    format: str = Query("json"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    access.ensure_can_run_report(me, access.REPORT_TIME_ENTRY)
    fmt = _validate_format(format)
    tid = me.tenant_id or ""

    if entity_id:
        await access.ensure_can_view_entity(db, me, entity_id)
    if user_id and user_id != me.id:
        await access.ensure_can_view_user(db, me, user_id)

    entity_scope = await access.resolve_report_entity_scope(db, me)
    user_scope = await access.resolve_report_user_scope(db, me)

    result = await time_entry_report(
        db,
        tenant_id=tid,
        entity_scope=entity_scope,
        user_scope=user_scope,
        start_date=start_date,
        end_date=end_date,
        entity_id=entity_id,
        user_id=user_id,
        period_id=period_id,
    )
    tenant_name = await _tenant_name(db, tid)
    entity_name = await _entity_label(db, tid, entity_id) if entity_id else None
    period_label = await _period_label(db, tid, period_id) if period_id else None
    parameters = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "entity_id": entity_id,
        "user_id": user_id,
        "period_id": period_id,
    }

    range_tag = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    if fmt == "csv":
        return await _csv_response(result, _csv_filename("time_entries", range_tag))
    if fmt == "pdf":
        return await _pdf_response(
            report_type=access.REPORT_TIME_ENTRY,
            me=me,
            tenant_name=tenant_name,
            result=result,
            filename=_pdf_filename("time_entries", range_tag),
            period_label=period_label,
            entity_name=entity_name,
            parameters=parameters,
        )
    return _json_envelope(
        report_type=access.REPORT_TIME_ENTRY,
        me=me,
        tenant_id=tid,
        tenant_name=tenant_name,
        result=result,
        period_id=period_id,
        period_label=period_label,
        entity_id=entity_id,
        entity_name=entity_name,
        parameters=parameters,
    )


# ============================================================
# Snapshots — immutable persisted records
# ============================================================
@router.get("/snapshots", response_model=list[ReportSnapshotOut])
async def list_snapshots(
    report_type: Optional[str] = Query(None),
    period_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """List recent snapshots in the tenant, newest first."""
    if me.role.value == "TEAM_MEMBER":
        raise HTTPException(
            status_code=403,
            detail="Team Members cannot browse report snapshots",
        )
    filters = [ReportSnapshot.tenant_id == me.tenant_id]
    if report_type:
        filters.append(ReportSnapshot.report_type == report_type)
    if period_id:
        filters.append(ReportSnapshot.period_id == period_id)
    rows = await db.execute(
        select(ReportSnapshot)
        .where(*filters)
        .order_by(ReportSnapshot.generated_at.desc())
        .limit(limit)
    )
    return [ReportSnapshotOut.model_validate(r) for r in rows.scalars().all()]


@router.get("/snapshots/{snapshot_id}", response_model=ReportSnapshotOut)
async def get_snapshot(
    snapshot_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one snapshot — RBAC: must be in the caller's tenant + role >= TEAM_LEAD."""
    if me.role.value == "TEAM_MEMBER":
        raise HTTPException(
            status_code=403,
            detail="Team Members cannot browse report snapshots",
        )
    row = await db.execute(
        select(ReportSnapshot).where(
            ReportSnapshot.id == snapshot_id,
            ReportSnapshot.tenant_id == me.tenant_id,
        )
    )
    snap = row.scalar_one_or_none()
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return ReportSnapshotOut.model_validate(snap)
