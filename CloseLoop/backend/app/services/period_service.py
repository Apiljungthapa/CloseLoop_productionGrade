"""Period service — manages tenant-wide period generation + closure."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from ..models import (
    Period,
    PeriodCadence,
    PeriodConfiguration,
    PeriodStatus,
    Tenant,
)
from ..calendar import (
    BS_MONTHS,
    bs_month_range_ad,
    fiscal_year_of,
    format_bs,
    known_holidays,
    nepal_today,
    to_bs,
    working_days as bs_working_days,
)


async def get_or_create_config(db: AsyncSession, tenant_id: str) -> PeriodConfiguration:
    result = await db.execute(
        select(PeriodConfiguration).where(PeriodConfiguration.tenant_id == tenant_id)
    )
    cfg = result.scalar_one_or_none()
    if cfg:
        return cfg
    cfg = PeriodConfiguration(tenant_id=tenant_id)
    db.add(cfg)
    await db.flush()
    return cfg


def _next_dow_on_or_after(d: date, dow: int) -> date:
    """Return the next date >= d whose weekday == dow (0=Mon..6=Sun)."""
    delta = (dow - d.weekday()) % 7
    return d + timedelta(days=delta)


async def _latest_period(db: AsyncSession, tenant_id: str) -> Optional[Period]:
    result = await db.execute(
        select(Period)
        .where(Period.tenant_id == tenant_id)
        .order_by(desc(Period.start_date))
        .limit(1)
    )
    return result.scalar_one_or_none()


def _calendar_half_bounds(d: date) -> Tuple[date, date]:
    """First and last day of the AD calendar half-year containing `d`
    (Jan-Jun / Jul-Dec)."""
    if d.month <= 6:
        return date(d.year, 1, 1), date(d.year, 6, 30)
    return date(d.year, 7, 1), date(d.year, 12, 31)


def _bs_month_bounds(anchor: date) -> Tuple[date, date]:
    """AD bounds of the BS month containing `anchor`."""
    bs = to_bs(anchor)
    return bs_month_range_ad(bs.year, bs.month)


def _bs_fiscal_group_bounds(anchor: date, groups_fn) -> Tuple[date, date]:
    """Shared helper for BS_QUARTER / BS_HALF_YEAR: find which group of BS
    (year, month) pairs — from `fiscal_year_of(...).quarters()` or
    `.halves()` — contains `anchor`'s BS month, and return the AD bounds
    spanning that group's first through last month."""
    bs = to_bs(anchor)
    fy = fiscal_year_of(bs)
    for group in groups_fn(fy):
        if (bs.year, bs.month) in group:
            start = bs_month_range_ad(*group[0])[0]
            end = bs_month_range_ad(*group[-1])[1]
            return start, end
    raise AssertionError(f"BS {bs.year}-{bs.month:02d} not found in its own fiscal year")


def _bs_quarter_bounds(anchor: date) -> Tuple[date, date]:
    return _bs_fiscal_group_bounds(anchor, lambda fy: fy.quarters())


def _bs_half_bounds(anchor: date) -> Tuple[date, date]:
    return _bs_fiscal_group_bounds(anchor, lambda fy: fy.halves())


def _calendar_year_bounds(d: date) -> Tuple[date, date]:
    """First and last day of the AD calendar year containing `d`."""
    return date(d.year, 1, 1), date(d.year, 12, 31)


def _bs_year_bounds(anchor: date) -> Tuple[date, date]:
    """AD bounds of the whole Nepali fiscal year (Shrawan 1 .. end of
    Ashadh) containing `anchor` — i.e. the FiscalYear itself, treated as
    one annual period."""
    fy = fiscal_year_of(to_bs(anchor))
    return fy.ad_start, fy.ad_end


def _bs_fiscal_group_index(anchor: date, groups_fn) -> int:
    """1-based index of the group (quarter/half) containing `anchor`."""
    bs = to_bs(anchor)
    fy = fiscal_year_of(bs)
    for i, group in enumerate(groups_fn(fy), start=1):
        if (bs.year, bs.month) in group:
            return i
    raise AssertionError(f"BS {bs.year}-{bs.month:02d} not found in its own fiscal year")


def _compute_label(
    start: date, end: date, index: int, cadence: PeriodCadence = PeriodCadence.FIXED_WEEKS
) -> str:
    """Label format follows cadence — "P001 ..." only ever made sense for
    the fixed-week pattern; calendar-shaped periods get a label that
    matches what they actually are."""
    if cadence == PeriodCadence.CALENDAR_MONTH:
        return start.strftime("%b %Y")
    if cadence == PeriodCadence.CALENDAR_QUARTER:
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if cadence == PeriodCadence.CALENDAR_HALF_YEAR:
        return f"H{1 if start.month <= 6 else 2} {start.year}"
    if cadence == PeriodCadence.CALENDAR_YEAR:
        return f"{start.year}"
    if cadence == PeriodCadence.BS_YEAR:
        return f"FY {fiscal_year_of(to_bs(start)).label}"
    if cadence == PeriodCadence.BS_MONTH:
        bs = to_bs(start)
        return f"{BS_MONTHS[bs.month]} {bs.year}"
    if cadence == PeriodCadence.BS_QUARTER:
        idx = _bs_fiscal_group_index(start, lambda fy: fy.quarters())
        return f"Q{idx} FY {fiscal_year_of(to_bs(start)).label}"
    if cadence == PeriodCadence.BS_HALF_YEAR:
        idx = _bs_fiscal_group_index(start, lambda fy: fy.halves())
        return f"H{idx} FY {fiscal_year_of(to_bs(start)).label}"
    return f"P{index:03d} {start.strftime('%d-%b-%Y')} – {end.strftime('%d-%b-%Y')}"


def _month_bounds(d: date) -> Tuple[date, date]:
    """First and last day of the calendar month containing `d`."""
    start = d.replace(day=1)
    next_month_start = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, next_month_start - timedelta(days=1)


def _quarter_bounds(d: date) -> Tuple[date, date]:
    """First and last day of the calendar quarter containing `d`."""
    q_start_month = ((d.month - 1) // 3) * 3 + 1
    start = date(d.year, q_start_month, 1)
    end_month = q_start_month + 2
    end_year = d.year + (end_month > 12)
    end_month = end_month - 12 if end_month > 12 else end_month
    _, end = _month_bounds(date(end_year, end_month, 1))
    return start, end


def _period_bounds(
    cadence: PeriodCadence, anchor: date, duration_weeks: int,
    start_day_of_week: Optional[int] = None,
) -> Tuple[date, date]:
    """Bounds of the period that STARTS AT (or, for calendar cadences,
    contains) `anchor` — the shared "what does one period of this cadence
    look like" rule used both for the first period and every subsequent
    one, so the schedule stays self-consistent regardless of cadence.

    For FIXED_WEEKS, when `start_day_of_week` is given, the naive
    `duration_weeks`-long end is stretched FORWARD (never shrunk) to the
    next occurrence of the day before `start_day_of_week` — e.g.
    start_day_of_week=Friday closes every period on Thursday. `anchor`
    itself is never moved. The very first period is usually the only one
    this actually changes anything for: if the admin's chosen start date
    doesn't already sit on that weekly phase, its naive end lands on the
    wrong weekday, so it absorbs a few extra days as a one-off longer
    "stub" period rather than leaving them uncovered by any period at
    all. Every period after that starts exactly on `start_day_of_week`
    by construction, so its own naive end already lands on the target
    weekday and this is a no-op — the schedule locks into a clean,
    uniform `duration_weeks` length from period 2 onward, forever."""
    if cadence == PeriodCadence.CALENDAR_MONTH:
        return _month_bounds(anchor)
    if cadence == PeriodCadence.CALENDAR_QUARTER:
        return _quarter_bounds(anchor)
    if cadence == PeriodCadence.CALENDAR_HALF_YEAR:
        return _calendar_half_bounds(anchor)
    if cadence == PeriodCadence.CALENDAR_YEAR:
        return _calendar_year_bounds(anchor)
    if cadence == PeriodCadence.BS_MONTH:
        return _bs_month_bounds(anchor)
    if cadence == PeriodCadence.BS_QUARTER:
        return _bs_quarter_bounds(anchor)
    if cadence == PeriodCadence.BS_HALF_YEAR:
        return _bs_half_bounds(anchor)
    if cadence == PeriodCadence.BS_YEAR:
        return _bs_year_bounds(anchor)
    end = anchor + timedelta(days=duration_weeks * 7 - 1)
    if start_day_of_week is not None:
        end = _next_dow_on_or_after(end, (start_day_of_week - 1) % 7)
    return anchor, end


async def ensure_initial_period(db: AsyncSession, tenant_id: str) -> Period:
    """Make sure the tenant has at least one period. Creates one starting
    today (fixed-week cadence still anchors to the next config-DOW first;
    calendar cadences just use today's own month/quarter)."""
    existing = await _latest_period(db, tenant_id)
    if existing:
        return existing
    cfg = await get_or_create_config(db, tenant_id)
    today = nepal_today()
    anchor = (
        _next_dow_on_or_after(today, cfg.start_day_of_week)
        if cfg.cadence == PeriodCadence.FIXED_WEEKS
        else today
    )
    start, end = _period_bounds(cfg.cadence, anchor, cfg.duration_weeks)
    period = Period(
        tenant_id=tenant_id,
        label=_compute_label(start, end, 1, cfg.cadence),
        start_date=start,
        end_date=end,
        status=PeriodStatus.OPEN,
    )
    db.add(period)
    await db.flush()
    return period


async def create_next_period(
    db: AsyncSession, tenant_id: str, current: Period
) -> Period:
    cfg = await get_or_create_config(db, tenant_id)
    next_start = current.end_date + timedelta(days=1)
    _, next_end = _period_bounds(cfg.cadence, next_start, cfg.duration_weeks)

    # naive label index = count of periods + 1
    result = await db.execute(
        select(Period.id).where(
            Period.tenant_id == tenant_id
        )
    )
    idx = len(result.all()) + 1

    nxt = Period(
        tenant_id=tenant_id,
        label=_compute_label(next_start, next_end, idx, cfg.cadence),
        start_date=next_start,
        end_date=next_end,
        status=PeriodStatus.OPEN,
    )
    db.add(nxt)
    await db.flush()
    current.next_period_id = nxt.id
    await db.flush()
    return nxt


@dataclass
class _GeneratedSeed:
    """Duck-typed seed for apply_period_schedule (label/start_date/end_date)."""

    label: str
    start_date: date
    end_date: date


def _build_seeds(
    cadence: PeriodCadence, start_date: date, period_count: int, weeks: int,
    start_day_of_week: Optional[int] = None,
) -> list[_GeneratedSeed]:
    """Pure date-math core shared by `generate_period_schedule` (persists
    the result) and `preview_period_schedule` (just enriches it for
    display) — so the two can never drift out of sync.

    `start_date` is used exactly as given — see `_period_bounds` for how
    `start_day_of_week` (when passed, FIXED_WEEKS only) locks every period
    onto a consistent weekly phase: period 1 may come out longer than
    `weeks` if `start_date` doesn't already sit on that phase (a one-off
    "stub" that keeps every day covered by some period instead of
    silently dropping the days before the next aligned start), and every
    period after it is a clean, uniform `weeks`-long block.
    """
    seeds: list[_GeneratedSeed] = []
    cur_start, cur_end = _period_bounds(cadence, start_date, weeks, start_day_of_week)
    for idx in range(1, period_count + 1):
        seeds.append(
            _GeneratedSeed(
                label=_compute_label(cur_start, cur_end, idx, cadence),
                start_date=cur_start,
                end_date=cur_end,
            )
        )
        cur_start = cur_end + timedelta(days=1)
        cur_start, cur_end = _period_bounds(cadence, cur_start, weeks, start_day_of_week)
    return seeds


async def generate_period_schedule(
    db: AsyncSession,
    tenant_id: str,
    cadence: PeriodCadence,
    start_date: date,
    period_count: int,
    duration_weeks: Optional[int] = None,
    start_day_of_week: Optional[int] = None,
) -> list[Period]:
    """
    Build `period_count` sequential periods for `cadence` starting at
    `start_date`, correctly labeled for that cadence, and persist them via
    `apply_period_schedule` (replaces the tenant's existing periods,
    chains next_period_id, derives OPEN/CLOSED from today) — the
    universal, reusable counterpart to the hardcoded 2026 retail-calendar
    seed: works for any tenant, any start date, any cadence (fixed-week,
    calendar or Bikram Sambat).
    """
    cfg = await get_or_create_config(db, tenant_id)
    weeks = duration_weeks or cfg.duration_weeks
    dow = start_day_of_week if start_day_of_week is not None else cfg.start_day_of_week
    seeds = _build_seeds(cadence, start_date, period_count, weeks, dow)
    return await apply_period_schedule(db, tenant_id, seeds)


def bs_labels_for(start: date, end: date) -> dict:
    """BS start/end label + ISO strings for a Period's AD start/end dates —
    used to enrich PeriodOut so the UI can show a BS Dates column and the
    calendar widget without a round trip per row."""
    bs_start, bs_end = to_bs(start), to_bs(end)
    return {
        "bs_start_label": format_bs(bs_start, "short"),
        "bs_end_label": format_bs(bs_end, "short"),
        "bs_start_iso": format_bs(bs_start, "iso"),
        "bs_end_iso": format_bs(bs_end, "iso"),
    }


def preview_period_schedule(
    cadence: PeriodCadence,
    start_date: date,
    period_count: int,
    duration_weeks: Optional[int] = None,
    start_day_of_week: Optional[int] = None,
) -> list[dict]:
    """Dry-run counterpart of `generate_period_schedule` — same date math
    (via `_build_seeds`, including the FIXED_WEEKS weekday snap), nothing
    persisted, each seed enriched with BS labels, working-day count, the
    Nepal-calendar closing date (last working day) and any public holidays
    inside it. Powers the "preview before you commit" step in the
    period-generation UI — must apply the exact same snap `generate_period_
    schedule` does, or the preview would show a different first period than
    what actually gets created."""
    weeks = duration_weeks or 4
    seeds = _build_seeds(cadence, start_date, period_count, weeks, start_day_of_week)
    if not seeds:
        return []

    years = sorted({y for s in seeds for y in (s.start_date.year, s.end_date.year)})
    holiday_map = known_holidays(range(years[0], years[-1] + 1))

    out: list[dict] = []
    for s in seeds:
        wd = bs_working_days(s.start_date, s.end_date)
        holidays_in = sorted(
            (d, n) for d, n in holiday_map.items() if s.start_date <= d <= s.end_date
        )
        closing = wd[-1].ad if wd else s.end_date
        out.append(
            {
                "label": s.label,
                "start_date": s.start_date,
                "end_date": s.end_date,
                **bs_labels_for(s.start_date, s.end_date),
                "total_days": (s.end_date - s.start_date).days + 1,
                "working_days": len(wd),
                "closing_date": closing,
                "holidays": [{"date": d.isoformat(), "name": n} for d, n in holidays_in],
            }
        )
    return out


async def get_current_period(db: AsyncSession, tenant_id: str) -> Optional[Period]:
    """The tenant's "current" period: the OPEN/IN_PROGRESS one whose date
    range actually covers today, or — if none does (e.g. the admin is
    working a few days ahead of the period's start) — the earliest
    OPEN/IN_PROGRESS one. Mirrors the frontend's own `resolveCurrentPeriodId`
    (leadDashUtils.js) so both sides agree on which period is "current".

    The date-range check matters whenever more than one period is sitting
    in OPEN/IN_PROGRESS at once (e.g. an old period the close-scheduler
    hasn't rolled over yet, alongside the new one that's actually running)
    — without it, sorting by `start_date` alone would always return the
    OLDER, already-elapsed period instead of the one covering today, which
    made "Once per Period" due-date previews (and the sub-task ≤-parent
    validation) clamp against that stale period's much-earlier end date.
    """
    rows = (
        await db.execute(
            select(Period).where(
                Period.tenant_id == tenant_id,
                Period.status.in_([PeriodStatus.OPEN, PeriodStatus.IN_PROGRESS]),
            )
        )
    ).scalars().all()
    if not rows:
        return None
    today = nepal_today()
    containing = [p for p in rows if p.start_date <= today <= p.end_date]
    if containing:
        return max(containing, key=lambda p: p.start_date)
    return min(rows, key=lambda p: p.start_date)


async def get_next_defined_period(
    db: AsyncSession, tenant_id: str, current: Period
) -> Optional[Period]:
    """Find the next pre-defined period without creating a new one."""
    if current.next_period_id:
        result = await db.execute(
            select(Period)
            .where(
                Period.tenant_id == tenant_id,
                Period.id == current.next_period_id,
            )
        )
        next_period = result.scalar_one_or_none()
        if next_period:
            return next_period

    # Fallback: find the nearest period that starts after the current period ends.
    result = await db.execute(
        select(Period)
        .where(
            Period.tenant_id == tenant_id,
            Period.start_date > current.end_date,
        )
        .order_by(Period.start_date)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active_period_by_date(
    db: AsyncSession, tenant_id: str, today: Optional[date] = None
) -> Optional[Period]:
    """Find the period whose date range covers `today`, regardless of status.

    Useful for the 2026 Retail Calendar where status may not yet have
    flipped to OPEN/CLOSED via the scheduler — the date is the source
    of truth for "what period are we in right now?".
    """
    today = today or nepal_today()
    result = await db.execute(
        select(Period)
        .where(
            Period.tenant_id == tenant_id,
            Period.start_date <= today,
            Period.end_date >= today,
        )
        .order_by(desc(Period.start_date))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def apply_period_schedule(
    db: AsyncSession,
    tenant_id: str,
    seeds: Sequence[Any],
    today: Optional[date] = None,
) -> list[Period]:
    """Replace the tenant's entire period schedule with an explicit ordered
    list of seeds (each exposing `.label`, `.start_date`, `.end_date` —
    duck-typed so both PeriodSeedIn instances and file-parsed rows work).

    Chains `next_period_id` in order, then derives status from `today`:
    the period covering today (if any) is OPEN, everything else CLOSED —
    falling back to the most recently ended period, or the first period
    if none has started yet. Shared by the paste-text (`/import`) and
    file-upload (`/import-file`) period import flows so both behave
    identically.
    """
    today = today or nepal_today()

    existing = await db.execute(
        select(Period).where(
            Period.tenant_id == tenant_id
        )
    )
    for p in existing.scalars().all():
        await db.delete(p)
    await db.flush()

    created: list[Period] = []
    prev: Optional[Period] = None
    for item in seeds:
        p = Period(
            tenant_id=tenant_id,
            label=item.label,
            start_date=item.start_date,
            end_date=item.end_date,
            status=PeriodStatus.CLOSED,
        )
        db.add(p)
        await db.flush()
        if prev is not None:
            prev.next_period_id = p.id
            await db.flush()
        prev = p
        created.append(p)

    current: Optional[Period] = None
    for p in created:
        if p.start_date <= today <= p.end_date:
            p.status = PeriodStatus.OPEN
            current = p
        else:
            p.status = PeriodStatus.CLOSED
    if current is None and created:
        past = [p for p in created if p.end_date < today]
        if past:
            past[-1].status = PeriodStatus.OPEN
        else:
            created[0].status = PeriodStatus.OPEN

    await db.flush()
    return created


def due_date_for(period: Period, due_day_of_period: Optional[int]) -> date:
    """Convert 1-based day index in a period into an actual date.

    A missing (None) or out-of-range index falls back to day 1 — a
    SubTaskTemplate leaves `due_day_of_period` nullable, and legacy pack
    snapshots predate the not-null default on TaskTemplate.
    """
    if not due_day_of_period or due_day_of_period < 1:
        due_day_of_period = 1
    candidate = period.start_date + timedelta(days=due_day_of_period - 1)
    if candidate > period.end_date:
        return period.end_date
    return candidate
