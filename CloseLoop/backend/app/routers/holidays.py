"""Holidays — the days the platform is closed on top of Saturday/Sunday.
Every holiday recorded here is skipped exactly like the weekend (and is the
only thing the calendars mark as a holiday).

Any signed-in user may READ them (the date pickers grey them out and the
overdue labels skip them). Writing is Platform Owner only: the list is global,
shared by every tenant, so one tenant's admin must not be able to move another
tenant's due dates.

Every write reloads the in-process registry (calendar/holiday_registry) at
once, so the new holiday applies to the next due date computed — no restart.
Holidays do not move tasks that already exist; they apply to dates generated
or picked from then on (and to not-yet-started template tasks the next time
their Close Pack is synced).
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_roles
from ..database import get_db
from ..models import Holiday, Role, User
from ..schemas import HolidayBulkIn, HolidayBulkOut, HolidayIn, HolidayOut
from ..services.holiday_service import (
    MANUAL,
    holiday_key,
    new_holiday,
    refresh_holiday_registry,
    upsert_holidays,
)

router = APIRouter(prefix="/api/holidays", tags=["holidays"])

_owner_only = Depends(require_roles(Role.PLATFORM_OWNER))


@router.get("", response_model=List[HolidayOut])
async def list_holidays(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every configured holiday (optionally only those in [from, to]),
    oldest first."""
    stmt = select(Holiday).order_by(Holiday.holiday_date)
    if from_date:
        stmt = stmt.where(Holiday.holiday_date >= from_date)
    if to_date:
        stmt = stmt.where(Holiday.holiday_date <= to_date)
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=HolidayOut, status_code=201, dependencies=[_owner_only])
async def create_holiday(payload: HolidayIn, db: AsyncSession = Depends(get_db)):
    """Add one holiday. Several holidays can share a date (they are one
    non-working day); 409 only when that date already has a holiday of the
    same name."""
    same_day = (
        await db.execute(select(Holiday).where(Holiday.holiday_date == payload.holiday_date))
    ).scalars().all()
    key = holiday_key(payload.holiday_date, payload.name)
    if any(holiday_key(h.holiday_date, h.name) == key for h in same_day):
        raise HTTPException(
            409, f"{payload.holiday_date.isoformat()} already has a holiday named {payload.name}"
        )
    holiday = new_holiday(payload, MANUAL)
    db.add(holiday)
    try:
        await db.commit()
    except IntegrityError:  # lost a race with another request for the same holiday
        await db.rollback()
        raise HTTPException(
            409, f"{payload.holiday_date.isoformat()} already has a holiday named {payload.name}"
        )
    await db.refresh(holiday)
    await refresh_holiday_registry(db)
    return holiday


@router.post("/bulk", response_model=HolidayBulkOut, dependencies=[_owner_only])
async def bulk_upsert_holidays(payload: HolidayBulkIn, db: AsyncSession = Depends(get_db)):
    """Add or update many holidays in one go, matched by date + name — so
    sending the same list again changes nothing. This is also the hook for a
    public-holiday feed: post what it returns with ``source: "API"`` (it never
    overwrites or duplicates a MANUAL holiday)."""
    created, updated, skipped = await upsert_holidays(db, payload.holidays, source=payload.source)
    await db.commit()
    await refresh_holiday_registry(db)
    return HolidayBulkOut(created=created, updated=updated, skipped=skipped)


@router.delete("/{holiday_id}", status_code=204, dependencies=[_owner_only])
async def delete_holiday(holiday_id: str, db: AsyncSession = Depends(get_db)):
    holiday = await db.get(Holiday, holiday_id)
    if not holiday:
        raise HTTPException(404, "Holiday not found")
    await db.delete(holiday)
    await db.commit()
    await refresh_holiday_registry(db)
    return Response(status_code=204)
