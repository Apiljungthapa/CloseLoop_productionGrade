"""Configured holidays — the `holidays` table behind
`calendar.holiday_registry`.

Everything that adds holidays goes through `upsert_holidays`, whatever the
source: the Platform Owner's Holidays page (POST /api/holidays), a bulk POST,
the seeded 2083 BS list (migration 0056), or — once there is one — a job that
pulls a public-holiday API and passes the dates it gets here with
``source=API``. Nothing else in the app needs to know where a holiday came
from; after a write, `refresh_holiday_registry` reloads the in-process registry
that the due-date rules, recurrence, overdue counts and calendar endpoints all
read.

A holiday is identified by its date AND name, so several holidays can share a
date (they are one non-working day, never a double shift) while the same
holiday sent twice is one row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import HolidayEntry, bs_iso, set_holiday_entries
from ..database import AsyncSessionLocal
from ..models import Holiday

logger = logging.getLogger(__name__)

MANUAL = "MANUAL"
API = "API"
ALL = "ALL"


@dataclass(frozen=True)
class HolidayDraft:
    """A holiday to store. ``category`` ALL is everyone's day off; GENDER /
    RELIGION carry an ``audience`` (who it is for)."""

    holiday_date: date
    name: str
    category: str = ALL
    audience: Optional[str] = None
    remarks: Optional[str] = None


def _draft(entry: Union[HolidayDraft, tuple]) -> HolidayDraft:
    """Accept a ready draft, a request body (anything with the same fields) or
    the plain ``(date, name)`` pair older callers pass."""
    if isinstance(entry, HolidayDraft):
        return entry
    if isinstance(entry, tuple):
        return HolidayDraft(entry[0], entry[1])
    return HolidayDraft(
        entry.holiday_date,
        entry.name,
        getattr(entry, "category", ALL),
        getattr(entry, "audience", None),
        getattr(entry, "remarks", None),
    )


def holiday_key(day: date, name: str) -> tuple:
    """What makes two holidays the same one: the date and the name, ignoring
    case and surrounding spaces."""
    return day, name.strip().casefold()


async def refresh_holiday_registry(db: AsyncSession) -> int:
    """Reload the in-process registry from the table. Returns how many
    holiday rows are now configured."""
    rows = (await db.execute(select(Holiday))).scalars().all()
    set_holiday_entries(
        HolidayEntry(h.holiday_date, h.name, h.category or ALL, h.audience) for h in rows
    )
    return len(rows)


async def refresh_holiday_registry_safely() -> None:
    """`refresh_holiday_registry` on its own session, for startup and the
    scheduler. Never raises: if the table can't be read (not migrated yet, DB
    blip) the holidays loaded last time stay in force instead of the app
    failing to boot or the registry being emptied."""
    try:
        async with AsyncSessionLocal() as db:
            count = await refresh_holiday_registry(db)
        logger.info("Holiday registry loaded (%d holiday(s))", count)
    except Exception:
        logger.exception("Could not refresh the holiday registry; keeping the last loaded holidays")


def _apply(row: Holiday, d: HolidayDraft, source: str) -> bool:
    """Copy the draft onto the row; True when anything actually changed."""
    wanted = {
        "name": d.name.strip(),
        "bs_date": bs_iso(d.holiday_date),
        "category": d.category,
        "audience": d.audience,
        "remarks": d.remarks,
        "source": source,
    }
    changed = False
    for field, value in wanted.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    return changed


def new_holiday(entry: Union[HolidayDraft, tuple], source: str = MANUAL) -> Holiday:
    """An unsaved Holiday row for this entry (BS date filled in from the AD one)."""
    d = _draft(entry)
    row = Holiday(holiday_date=d.holiday_date, name=d.name.strip(), source=source)
    _apply(row, d, source)
    return row


async def upsert_holidays(
    db: AsyncSession,
    entries: Iterable[Union[HolidayDraft, tuple]],
    *,
    source: str = MANUAL,
) -> tuple[int, int, int]:
    """Insert or update holidays, keyed by date + name (the last entry for a
    key wins, so the same holiday listed twice is one row). Returns
    ``(created, updated, skipped)``.

    A batch from the API source never overwrites or duplicates a MANUAL row:
    an API holiday whose date already has a MANUAL holiday of the same
    category is skipped, so an admin's correction survives the next sync.

    Flushes but does not commit; the caller commits, then calls
    `refresh_holiday_registry`."""
    wanted: dict = {}
    for entry in entries:
        d = _draft(entry)
        wanted[holiday_key(d.holiday_date, d.name)] = d
    if not wanted:
        return 0, 0, 0
    days = {key[0] for key in wanted}
    rows = await db.execute(select(Holiday).where(Holiday.holiday_date.in_(list(days))))
    by_date: dict = {}
    for h in rows.scalars().all():
        by_date.setdefault(h.holiday_date, []).append(h)

    created = updated = skipped = 0
    for key, d in wanted.items():
        on_day = by_date.get(d.holiday_date, [])
        row = next((h for h in on_day if holiday_key(h.holiday_date, h.name) == key), None)
        if source == API and (
            (row is not None and row.source == MANUAL)
            or (row is None and any(h.source == MANUAL and h.category == d.category for h in on_day))
        ):
            skipped += 1
        elif row is None:
            row = new_holiday(d, source)
            db.add(row)
            by_date.setdefault(d.holiday_date, []).append(row)
            created += 1
        elif _apply(row, d, source):
            updated += 1
    await db.flush()
    return created, updated, skipped
