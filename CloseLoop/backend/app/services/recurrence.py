"""Recurrence dates for a Close Pack task / recurring custom task.

A task carries an optional `frequency` plus a `recurrence_start` (the anchor
= first occurrence) and optional `recurrence_end`:

  * NULL       -> not recurring. One task per period on "due day N".
  * DAILY      -> recurrence_start, +interval days, +2*interval days, ...
  * WEEKLY     -> the `days_of_week` weekdays, every `interval` weeks
                 counting from recurrence_start's week.
  * MONTHLY    -> recurrence_start's day-of-month, every month.
  * QUARTERLY  -> recurrence_start's day, every 3 months.
  * SEMI_ANNUAL-> every 6 months.
  * ANNUAL     -> every 12 months.

DAILY and WEEKLY never produce an occurrence on a weekend day (Saturday/
Sunday — `calendar.NEPAL_WEEKEND`) or a configured holiday
(`calendar.holiday_registry`). This is enforced here, in the one primitive both
the preview and real generation call, so legacy templates and bulk imports that
still carry Sat/Sun are covered too. What happens to the occurrence differs:

  * DAILY — the cadence stays anchored on `recurrence_start` (every 4 days from
    a Tuesday is Tue, Sat, Wed, Sun, Thu, ...), and an occurrence that lands on
    a non-working day happens on the NEXT WORKING DAY instead (Sat -> Mon). It
    is moved, not dropped: dropping it left an 8-day hole in an "every 4 days"
    task each time the cadence stepped onto a weekend. When two settle on the
    same day (a plain every-day task: Sat, Sun and Mon all -> Mon) they are one
    task, so "every day" is simply every working day.
  * WEEKLY — only a legacy Sat/Sun weekday is dropped. An occurrence that lands
    on a holiday is KEPT: skipping it would silently lose the whole week's
    task. Like the monthly-and-longer cadences below, it happens on its raw date
    and only its DUE date moves to the next working day.

MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL are NOT filtered: an occurrence on
a Saturday/Sunday/holiday still happens, on that raw date (it fixes which
period it belongs to and its identity). It is its DUE date that moves to the
next working day — see due_date_rules.apply_due_days.

`interval_count` multiplies the base step for EVERY frequency — it is the
"Custom" knob: MONTHLY + interval_count=2 -> every 2 months, QUARTERLY +
interval_count=2 -> every 6 months, DAILY + interval_count=10 -> every 10
days, and so on.

`occurrence_dates()` returns every occurrence in a date range; the plan
generator then drops each one into whichever tenant period contains it.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import List, Optional, Sequence

from ..calendar import (
    NEPAL_WEEKEND,
    bs_add_months,
    bs_month_length,
    next_working_day,
    to_ad,
    to_bs,
)
from ..models import CalendarType, RecurrenceFrequency

F = RecurrenceFrequency

# How far before a range an occurrence can start and still be moved into it by
# a run of weekend/holiday days (a weekend plus a long holiday stretch).
_ROLL_LOOKBACK_DAYS = 31

# Sub-period frequencies land many times inside one (monthly-ish) period.
SUBPERIOD = (F.DAILY, F.WEEKLY)

_MONTH_STEP = {
    F.MONTHLY: 1,
    F.QUARTERLY: 3,
    F.SEMI_ANNUAL: 6,
    F.ANNUAL: 12,
}


def due_within(
    period_start: date,
    period_end: date,
    day_of_period: Optional[int],
    anchor: str = "PERIOD_START",
) -> date:
    """1-based day index -> a real date inside the period, clamped to the
    period bounds. Used for NON-recurring tasks (frequency = NULL).

      anchor = "PERIOD_START" (default): day 1 = period_start,
               day N = period_start + (N-1) days.
      anchor = "PERIOD_END":            day 1 = period_end (the last day),
               day N = period_end - (N-1) days — i.e. "N days from close".
    """
    n = day_of_period if (day_of_period and day_of_period >= 1) else 1
    # .value so a DueAnchor enum (straight off an ORM row) works too —
    # str(DueAnchor.PERIOD_END) is "DueAnchor.PERIOD_END" on Python < 3.11.
    if getattr(anchor, "value", anchor) == "PERIOD_END":
        candidate = period_end - timedelta(days=n - 1)
        return candidate if candidate >= period_start else period_start
    candidate = period_start + timedelta(days=n - 1)
    return candidate if candidate <= period_end else period_end


def _add_months_ad(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _add_months_bs(d: date, months: int) -> date:
    """Step by `months` BIKRAM SAMBAT months, keeping the same BS day-of-month
    (clamped to the target BS month's real length, 29-32 days)."""
    bs = to_bs(d)
    y, m = bs_add_months(bs.year, bs.month, months)
    day = min(bs.day, bs_month_length(y, m))
    return to_ad(y, m, day)


def occurrence_dates(
    *,
    frequency: Optional[RecurrenceFrequency],
    interval_count: int,
    days_of_week: Optional[Sequence[int]],
    start: date,
    end: Optional[date],
    range_start: date,
    range_end: date,
    calendar: CalendarType = CalendarType.AD,
) -> List[date]:
    """Every occurrence date in [range_start, range_end] (inclusive).

    DAILY / WEEKLY are calendar-agnostic (a day is a day, a week is 7 days).
    MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL step by *BS* months when
    `calendar == BS` (so a Nepali annual task recurs on the same Bikram
    Sambat date each fiscal year), otherwise by Gregorian months.
    """
    add_months = _add_months_bs if calendar == CalendarType.BS else _add_months_ad
    if frequency is None or start is None:
        return []
    interval = max(1, interval_count or 1)
    hard_end = min(range_end, end) if end else range_end
    if range_start > hard_end:
        return []

    out: List[date] = []

    if frequency == F.DAILY:
        # The cadence is fixed: start, start+interval, start+2*interval, ...
        # An occurrence that lands on a non-working day is MOVED to the next
        # working day, never dropped — dropping it turned "every 4 days" into
        # an 8-day hole each time the cadence stepped onto a weekend. Several
        # that settle on one day (a plain daily task over a weekend) are one
        # task. Start walking a little before range_start: an occurrence on
        # the weekend just before the range settles INSIDE it.
        first = max(range_start - timedelta(days=_ROLL_LOOKBACK_DAYS), start)
        delta = (first - start).days
        k = -(-delta // interval) if delta > 0 else 0
        d = start + timedelta(days=k * interval)
        settled = set()
        while d <= hard_end:
            on = next_working_day(d)
            if range_start <= on <= hard_end:
                settled.add(on)
            d += timedelta(days=interval)
        return sorted(settled)

    if frequency == F.WEEKLY:
        dows = set(days_of_week or []) - NEPAL_WEEKEND
        anchor_monday = start - timedelta(days=start.weekday())
        d = max(range_start, start)
        while d <= hard_end:
            if d >= start and d.weekday() in dows:
                this_monday = d - timedelta(days=d.weekday())
                week_delta = (this_monday - anchor_monday).days // 7
                if week_delta >= 0 and week_delta % interval == 0:
                    out.append(d)
            d += timedelta(days=1)
        return out

    step = _MONTH_STEP.get(frequency)
    if step:
        # walk months from `start` until past hard_end. `interval` lets a
        # "Custom" cadence stretch the base step (e.g. QUARTERLY x2 = every
        # 6 months, MONTHLY x2 = every other month).
        stride = step * interval
        i = 0
        while True:
            d = add_months(start, i * stride)
            if d > hard_end:
                break
            if d >= range_start and d >= start:
                out.append(d)
            i += 1
            if i > 600:  # ~50 years of monthly — pathological guard
                break
        return out

    return []
