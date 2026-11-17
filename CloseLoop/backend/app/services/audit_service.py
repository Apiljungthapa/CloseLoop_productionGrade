"""Audit log service — append-only writes to AuditLog."""

from __future__ import annotations
from typing import Optional, Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog


async def log_action(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    user_id: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    commit: bool = False,
) -> Optional[AuditLog]:
    """Record an audit entry. Caller is responsible for committing unless commit=True.

    Silently no-ops when tenant_id is missing (e.g. PLATFORM_OWNER-scoped actions);
    the AuditLog table requires a tenant.
    """
    if not tenant_id:
        return None
    log = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        meta=meta or {},
    )
    db.add(log)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return log
