"""Read-only integration surface for OAuth2 client-credentials callers.

Every route here is gated by `get_current_client_tenant` ONLY — no human
session (cookie or user bearer token) can authenticate against these
routes, and no client-credentials token can authenticate against the rest
of the app's routes (they're rejected by `get_current_user`, which only
accepts `type: "access"` tokens). The two credential spaces are fully
separate; this file is the entire surface a tenant's API client can reach.

Intentionally small today — periods + a plan summary. Extend by adding
more routes here, each still scoped to `tenant.id` from the resolved
client, never a client-supplied value.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Period, Plan, Tenant
from ..schemas import IntegrationPeriodOut, IntegrationPlanOut
from ..auth import get_current_client_tenant

router = APIRouter(prefix="/api/integration", tags=["integration"])


@router.get("/periods", response_model=list[IntegrationPeriodOut])
async def list_periods(
    tenant: Tenant = Depends(get_current_client_tenant),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(Period)
        .where(Period.tenant_id == tenant.id)
        .order_by(Period.start_date)
    )
    return [IntegrationPeriodOut.model_validate(p) for p in rows.scalars().all()]


@router.get("/plans", response_model=list[IntegrationPlanOut])
async def list_plans(
    tenant: Tenant = Depends(get_current_client_tenant),
    db: AsyncSession = Depends(get_db),
    period_id: str | None = None,
):
    filters = [Plan.tenant_id == tenant.id]
    if period_id:
        filters.append(Plan.period_id == period_id)
    rows = await db.execute(
        select(Plan).where(*filters).order_by(Plan.created_at.desc())
    )
    return [IntegrationPlanOut.model_validate(p) for p in rows.scalars().all()]
