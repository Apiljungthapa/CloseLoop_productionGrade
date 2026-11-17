"""HRMS integration — read-only lookups for the HRMS Integration page.

    GET /api/hrms/projects     -> [{id, name}, ...]  (sorted by name)
    GET /api/hrms/activities   -> [{id, name, description, sub_activities:
                                    [{id, name, key}]}, ...]  (HRMS order)

Any signed-in tenant user may call it. The HRMS credentials live in the
server environment; the browser never sees them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from ..auth import require_tenant
from ..models import User
from ..schemas import HrmsActivityOut, HrmsProjectOut
from ..services import hrms_service

router = APIRouter(prefix="/api/hrms", tags=["hrms"])


@router.get("/projects", response_model=list[HrmsProjectOut])
async def list_hrms_projects(me: User = Depends(require_tenant)):
    try:
        return await run_in_threadpool(hrms_service.get_projects)
    except hrms_service.HrmsNotConfigured:
        raise HTTPException(503, "The HRMS integration is not configured")
    except hrms_service.HrmsError as exc:
        raise HTTPException(502, str(exc))


@router.get("/activities", response_model=list[HrmsActivityOut])
async def list_hrms_activities(me: User = Depends(require_tenant)):
    try:
        return await run_in_threadpool(hrms_service.get_activities)
    except hrms_service.HrmsNotConfigured:
        raise HTTPException(503, "The HRMS integration is not configured")
    except hrms_service.HrmsError as exc:
        raise HTTPException(502, str(exc))
