"""Audit logs — read-only listing with filters."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuditLog, User, Role
from ..schemas import AuditLogOut
from ..auth import require_tenant

router = APIRouter(prefix="/api/audit-logs", tags=["audit-logs"])


@router.get("", response_model=list[AuditLogOut])
async def list_logs(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        raise HTTPException(403, "Audit logs require supervisor role")

    filters = [AuditLog.tenant_id == me.tenant_id]
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if action:
        filters.append(AuditLog.action == action)

    rows = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [AuditLogOut.model_validate(a) for a in rows.scalars().all()]


@router.get("/{log_id}", response_model=AuditLogOut)
async def get_log(
    log_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        raise HTTPException(403, "Audit logs require supervisor role")
    log = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.id == log_id, AuditLog.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not log:
        raise HTTPException(404, "Not found")
    return AuditLogOut.model_validate(log)
