"""Carry-forward groups — supervisor approval to copy items to next period."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import CarryForwardGroup, CarryForwardItem, User, Role
from ..schemas import (
    CarryForwardGroupOut,
    CarryForwardItemOut,
    CarryForwardDecisionIn,
)
from ..auth import require_tenant, require_roles
from ..services.carry_forward_service import apply_carry_forward_decision

router = APIRouter(prefix="/api/carry-forwards", tags=["carry-forwards"])


def _serialize_group(
    group: CarryForwardGroup, items: list[CarryForwardItem]
) -> CarryForwardGroupOut:
    return CarryForwardGroupOut(
        id=group.id,
        tenant_id=group.tenant_id,
        from_period_id=group.from_period_id,
        to_period_id=group.to_period_id,
        status=group.status,
        notes=group.notes,
        created_at=group.created_at,
        items=[CarryForwardItemOut.model_validate(i) for i in items],
    )


@router.get(
    "",
    response_model=list[CarryForwardGroupOut],
    dependencies=[
        Depends(require_roles(Role.SUPER_ADMIN, Role.TEAM_LEAD))
    ],
)
async def list_groups(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    from_period_id: str | None = None,
    status: str | None = None,
):
    filters = [CarryForwardGroup.tenant_id == me.tenant_id]
    if from_period_id:
        filters.append(CarryForwardGroup.from_period_id == from_period_id)
    if status:
        filters.append(CarryForwardGroup.status == status.upper())
    rows = await db.execute(
        select(CarryForwardGroup)
        .where(*filters)
        .order_by(CarryForwardGroup.created_at.desc())
    )
    groups = list(rows.scalars().all())

    # bulk-load items
    if not groups:
        return []
    gids = [g.id for g in groups]
    item_rows = await db.execute(
        select(CarryForwardItem).where(CarryForwardItem.group_id.in_(gids))
    )
    items = list(item_rows.scalars().all())
    items_by_group: dict[str, list[CarryForwardItem]] = {}
    for it in items:
        items_by_group.setdefault(it.group_id, []).append(it)
    return [_serialize_group(g, items_by_group.get(g.id, [])) for g in groups]


@router.get(
    "/{group_id}",
    response_model=CarryForwardGroupOut,
    dependencies=[
        Depends(require_roles(Role.SUPER_ADMIN, Role.TEAM_LEAD))
    ],
)
async def get_group(
    group_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    g = (
        await db.execute(
            select(CarryForwardGroup).where(
                CarryForwardGroup.id == group_id,
                CarryForwardGroup.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Group not found")
    item_rows = await db.execute(
        select(CarryForwardItem).where(CarryForwardItem.group_id == g.id)
    )
    return _serialize_group(g, list(item_rows.scalars().all()))


@router.post(
    "/{group_id}/decision",
    response_model=CarryForwardGroupOut,
    dependencies=[
        Depends(require_roles(Role.SUPER_ADMIN, Role.TEAM_LEAD))
    ],
)
async def decide(
    group_id: str,
    payload: CarryForwardDecisionIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    g = (
        await db.execute(
            select(CarryForwardGroup).where(
                CarryForwardGroup.id == group_id,
                CarryForwardGroup.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Group not found")
    try:
        g = await apply_carry_forward_decision(
            db,
            tenant_id=me.tenant_id,
            group=g,
            decision=payload.decision,
            user_id=me.id,
            notes=payload.notes,
            item_ids=payload.item_ids,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    item_rows = await db.execute(
        select(CarryForwardItem).where(CarryForwardItem.group_id == g.id)
    )
    return _serialize_group(g, list(item_rows.scalars().all()))
