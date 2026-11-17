"""Automated HRMS timesheet.

    GET  /api/timesheets/preview?hrms_project_id=&month=YYYY-MM[&through=]
    POST /api/timesheets/submit
    POST /api/timesheets/submissions/{id}/verify    (HRMS readable: CloseLoop checks)
    POST /api/timesheets/submissions/{id}/resolve   (not readable: the user says what happened)
    GET  /api/timesheets/submissions?hrms_project_id=&month=
    GET  /api/timesheets/obligations/me          (the reminder banner)

Everything is the caller's OWN timesheet: hours come from their time entries,
filed under the HRMS employee whose work e-mail equals theirs. Dates are English
(AD) throughout. Failures answer {"detail": {"msg", "code"}} so the UI can show
the message (existing getErrorMessage) and act on the code.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_tenant
from ..database import get_db
from ..models import User
from ..schemas import (
    TimesheetObligationOut,
    TimesheetPreviewOut,
    TimesheetResolveIn,
    TimesheetSubmissionOut,
    TimesheetSubmitIn,
)
from ..services import hrms_service, timesheet_reminder_service, timesheet_service as svc
from ..services.hrms_timesheet_gateway import TimesheetGateway, get_gateway

router = APIRouter(prefix="/api/timesheets", tags=["timesheets"])


def _http(exc: svc.TimesheetError) -> HTTPException:
    return HTTPException(exc.status_code, {"msg": exc.message, "code": exc.code})


async def _hrms(project_id: int) -> tuple[list[dict], Optional[dict]]:
    """(activities, this project incl. members) from the HRMS, off the event loop."""
    try:
        activities = await run_in_threadpool(hrms_service.get_activities)
        project = await run_in_threadpool(hrms_service.get_project_members, project_id)
    except hrms_service.HrmsNotConfigured:
        raise HTTPException(503, {"msg": "The HRMS integration is not configured", "code": "HRMS_NOT_CONFIGURED"})
    except hrms_service.HrmsError as exc:
        raise HTTPException(502, {"msg": str(exc), "code": "HRMS_ERROR"})
    return activities, project


def gateway_dep() -> TimesheetGateway:
    return get_gateway()


@router.get("/preview", response_model=TimesheetPreviewOut)
async def preview(
    hrms_project_id: int = Query(...),
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    through: Optional[date] = Query(None),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    gateway: TimesheetGateway = Depends(gateway_dep),
):
    try:
        start, end = svc.resolve_range(month, through)
        activities, project = await _hrms(hrms_project_id)
        return await svc.build_preview(
            db, me, project_id=hrms_project_id, month=month, start=start, end=end,
            hrms_activities=activities, hrms_project=project, gateway=gateway,
        )
    except svc.TimesheetError as exc:
        raise _http(exc)


@router.post("/submit", response_model=TimesheetSubmissionOut)
async def submit(
    body: TimesheetSubmitIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    gateway: TimesheetGateway = Depends(gateway_dep),
):
    try:
        start, end = svc.resolve_range(body.month, body.through)
        activities, project = await _hrms(body.hrms_project_id)
        return await svc.submit(
            db, me, project_id=body.hrms_project_id, month=body.month, start=start, end=end,
            choices=body.lines, idempotency_key=body.idempotency_key,
            acknowledge_warnings=body.acknowledge_warnings,
            hrms_activities=activities, hrms_project=project, gateway=gateway,
        )
    except svc.TimesheetError as exc:
        raise _http(exc)


@router.post("/submissions/{submission_id}/verify", response_model=TimesheetSubmissionOut)
async def verify(
    submission_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    gateway: TimesheetGateway = Depends(gateway_dep),
):
    try:
        return await svc.verify_uncertain(db, me, submission_id, gateway)
    except svc.TimesheetError as exc:
        raise _http(exc)


@router.post("/submissions/{submission_id}/resolve", response_model=TimesheetSubmissionOut)
async def resolve(
    submission_id: str,
    body: TimesheetResolveIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """For a HRMS CloseLoop can't read: the user says whether unknown entries arrived."""
    try:
        return await svc.resolve_uncertain(db, me, submission_id, body.outcome)
    except svc.TimesheetError as exc:
        raise _http(exc)


@router.get("/submissions", response_model=list[TimesheetSubmissionOut])
async def submissions(
    hrms_project_id: Optional[int] = Query(None),
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    try:
        start, end = svc.month_bounds(month) if month else (None, None)
    except svc.TimesheetError as exc:
        raise _http(exc)
    return await svc.list_submissions(db, me, hrms_project_id, start, end)


@router.get("/obligations/me", response_model=list[TimesheetObligationOut])
async def my_obligations(me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    return await timesheet_reminder_service.obligations_for_user(db, me)
