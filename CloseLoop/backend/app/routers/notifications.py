from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Notification, User
from ..schemas import NotificationOut
from ..auth import require_tenant

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    rows = (
        (
            await db.execute(
                select(Notification)
                .where(
                    Notification.tenant_id == me.tenant_id,
                    Notification.user_id == me.id,
                )
                .order_by(Notification.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [NotificationOut.model_validate(n) for n in rows]


@router.post("/{nid}/read")
async def mark_read(
    nid: str, me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    n = (
        await db.execute(
            select(Notification).where(
                Notification.id == nid,
                Notification.user_id == me.id,
                Notification.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Not found")
    n.read = True
    await db.commit()
    return {"ok": True}


@router.post("/mark-all-read")
async def mark_all_read(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        update(Notification)
        .where(
            Notification.tenant_id == me.tenant_id,
            Notification.user_id == me.id,
            Notification.read == False,
        )
        .values(read=True)
    )
    await db.commit()
    return {"ok": True, "updated": result.rowcount or 0}
