"""Nepali (Bikram Sambat) calendar — read-only endpoints powering the
Periods page's calendar widget: today's BS/AD date + fiscal year, a
Sun-first AD month grid annotated with BS day numbers / weekends / Nepal
public holidays (the library's estimates plus the configured ones — see
routers/holidays.py) / whichever tenant Period covers each day, and a fiscal
year's month/quarter/half breakdown for the period-generation UI.

See app/calendar/ for the underlying BS conversion + holiday logic, and
routers/periods.py for period generation itself (/periods/preview,
/periods/generate) and the .ics calendar-sync export.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import (
    BS_MONTHS,
    NEPAL_WEEKEND,
    FiscalYear,
    bs_add_months,
    bs_month_length,
    bs_today,
    fiscal_year_of,
    format_bs,
    is_non_working_day,
    known_holidays,
    nepal_today,
    to_ad,
    to_bs,
)
from ..database import get_db
from ..models import Period, User
from ..schemas import (
    BsConvertBatchIn,
    BsConvertBatchOut,
    BsConvertOut,
    BsMonthDayOut,
    BsMonthOut,
    CalendarDayOut,
    CalendarMonthOut,
    FiscalYearMonthOut,
    FiscalYearOut,
    TodayOut,
)
from ..auth import require_tenant
from ..services.period_service import bs_month_range_ad

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/today", response_model=TodayOut)
async def today(me: User = Depends(require_tenant)):
    bs = bs_today()
    fy = fiscal_year_of(bs)
    return TodayOut(
        ad_date=nepal_today(),
        bs_label=format_bs(bs, "short"),
        bs_year=bs.year,
        bs_month=bs.month,
        bs_day=bs.day,
        fiscal_year_label=fy.label,
        fiscal_year_start=fy.start_year,
    )


@router.get("/month", response_model=CalendarMonthOut)
async def month(
    year: int = Query(..., ge=1900, le=2200),
    month: int = Query(..., ge=1, le=12),
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """A Sun-first AD month grid — each day carries its BS date, weekend/
    holiday flags, and (if any) the tenant Period covering it, so the UI
    can color each cell by period status without a second round trip."""
    _, days_in_month = _stdlib_calendar.monthrange(year, month)
    first = date(year, month, 1)
    last = date(year, month, days_in_month)
    today_ad = nepal_today()

    holiday_map = known_holidays([year])

    periods = (
        (
            await db.execute(
                select(Period).where(
                    Period.tenant_id == me.tenant_id,
                    Period.start_date <= last,
                    Period.end_date >= first,
                )
            )
        )
        .scalars()
        .all()
    )

    days: list[CalendarDayOut] = []
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        bs = to_bs(d)
        covering = next((p for p in periods if p.start_date <= d <= p.end_date), None)
        days.append(
            CalendarDayOut(
                ad_date=d,
                bs_year=bs.year,
                bs_month=bs.month,
                bs_day=bs.day,
                bs_month_name=BS_MONTHS[bs.month],
                weekday=d.weekday(),
                is_weekend=d.weekday() in NEPAL_WEEKEND,  # Saturday + Sunday
                is_non_working=is_non_working_day(d),
                is_today=d == today_ad,
                holiday=holiday_map.get(d),
                period_id=covering.id if covering else None,
                period_label=covering.label if covering else None,
                period_status=covering.status.value if covering else None,
                period_start_date=covering.start_date if covering else None,
                period_end_date=covering.end_date if covering else None,
            )
        )

    # Sun-first leading blanks (Python's weekday(): Mon=0..Sun=6 -> Sun-first offset)
    leading = (first.weekday() + 1) % 7

    return CalendarMonthOut(
        ad_year=year,
        ad_month=month,
        ad_month_label=first.strftime("%B %Y"),
        leading_blanks=leading,
        days=days,
    )


@router.get("/convert", response_model=BsConvertOut)
async def convert(
    ad_date: date = Query(..., description="AD date to render on the Bikram Sambat calendar"),
    me: User = Depends(require_tenant),
):
    """Render one AD date on the BS calendar — the closed-field label for
    the Nepali date-picker."""
    bs = to_bs(ad_date)
    return BsConvertOut(
        ad_date=ad_date,
        bs_year=bs.year,
        bs_month=bs.month,
        bs_day=bs.day,
        bs_month_name=BS_MONTHS[bs.month],
        bs_label=f"{BS_MONTHS[bs.month]} {bs.day}, {bs.year}",
        bs_iso=format_bs(bs, "iso"),
    )


@router.post("/convert-batch", response_model=BsConvertBatchOut)
async def convert_batch(
    payload: BsConvertBatchIn,
    me: User = Depends(require_tenant),
):
    """Render many AD dates on the BS calendar in one round trip — lets a
    BS-mode pack's task table, detail panel, and assignment sections show
    every date in BS without one request per date. Same bs_label format as
    GET /convert, so it always matches what the date-picker's own closed
    field shows for that date. A date outside the supported BS range is
    silently left out of the response rather than failing the whole batch.
    """
    labels: dict[str, str] = {}
    for ad_date in set(payload.dates):
        try:
            bs = to_bs(ad_date)
        except Exception:
            continue
        labels[ad_date.isoformat()] = f"{BS_MONTHS[bs.month]} {bs.day}, {bs.year}"
    return BsConvertBatchOut(labels=labels)


@router.get("/bs-month", response_model=BsMonthOut)
async def bs_month(
    bs_year: int = Query(..., ge=1975, le=2100),
    bs_month: int = Query(..., ge=1, le=12),
    me: User = Depends(require_tenant),
):
    """A whole Bikram Sambat month laid out for a Sun-first picker grid —
    each day carries its real AD date so the caller keeps storing AD dates
    while the user navigates in BS. Prev/next step by BS month."""
    length = bs_month_length(bs_year, bs_month)
    first_ad = to_ad(bs_year, bs_month, 1)
    last_ad = to_ad(bs_year, bs_month, length)
    today_ad = nepal_today()

    holiday_map = known_holidays(sorted({first_ad.year, last_ad.year}))

    days: list[BsMonthDayOut] = []
    for d in range(1, length + 1):
        ad = to_ad(bs_year, bs_month, d)
        days.append(
            BsMonthDayOut(
                bs_day=d,
                ad_date=ad,
                weekday=ad.weekday(),
                is_weekend=ad.weekday() in NEPAL_WEEKEND,  # Saturday + Sunday
                is_non_working=is_non_working_day(ad),
                is_today=ad == today_ad,
                holiday=holiday_map.get(ad),
            )
        )

    # Sun-first leading blanks (Python weekday(): Mon=0..Sun=6 -> Sun-first)
    leading = (first_ad.weekday() + 1) % 7

    ad_range_label = f"{first_ad.strftime('%d %b')} – {last_ad.strftime('%d %b %Y')}"

    prev_y, prev_m = bs_add_months(bs_year, bs_month, -1)
    next_y, next_m = bs_add_months(bs_year, bs_month, 1)

    return BsMonthOut(
        bs_year=bs_year,
        bs_month=bs_month,
        bs_month_name=BS_MONTHS[bs_month],
        ad_range_label=ad_range_label,
        leading_blanks=leading,
        days=days,
        prev_bs_year=prev_y,
        prev_bs_month=prev_m,
        next_bs_year=next_y,
        next_bs_month=next_m,
    )


@router.get("/fiscal-year", response_model=FiscalYearOut)
async def fiscal_year(
    start_year: int = Query(..., ge=1975, le=2099, description="BS year Shrawan 1 falls in"),
    me: User = Depends(require_tenant),
):
    fy = FiscalYear(start_year)

    def _months_out(pairs) -> list[FiscalYearMonthOut]:
        out = []
        for bs_year, bs_month in pairs:
            ad_start, ad_end = bs_month_range_ad(bs_year, bs_month)
            out.append(
                FiscalYearMonthOut(
                    bs_year=bs_year,
                    bs_month=bs_month,
                    label=f"{BS_MONTHS[bs_month]} {bs_year}",
                    ad_start=ad_start,
                    ad_end=ad_end,
                )
            )
        return out

    return FiscalYearOut(
        start_year=fy.start_year,
        label=fy.label,
        bs_start_label=format_bs(fy.bs_start, "short"),
        bs_end_label=format_bs(fy.bs_end, "short"),
        ad_start=fy.ad_start,
        ad_end=fy.ad_end,
        months=_months_out(fy.months()),
        quarters=[_months_out(q) for q in fy.quarters()],
        halves=[_months_out(h) for h in fy.halves()],
    )
