"""When is a timesheet due, and which reminder is today's? (pure rules)

Timesheets are due at the end of BOTH calendars' months:

  * the English (AD) month  - last day of the calendar month
  * the Nepali (BS) month   - last day of the BS month (about the 14th-17th of
                              an AD month, so they fall at different times)

Each month is an "obligation" with its own deadline. Someone owes a timesheet
for a month while they have SUBMITTED tasks in it that were not yet filed to the
HRMS, so a single submission covers both calendars - sending a day settles that
day in the AD month and the BS month alike.

Reminders go out on working days only (weekend + recorded holidays, the same
rule task due dates follow). A deadline that falls on a day off is reminded on
the working day before it. After the deadline there is one reminder per
working day until nothing is left to send.
"""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from ..calendar import (
    BS_MAX_YEAR,
    BS_MIN_YEAR,
    BS_MONTHS,
    bs_add_months,
    bs_month_range_ad,
    is_non_working_day,
    previous_working_day,
    to_bs,
)

AD, BS = "AD", "BS"

UPCOMING_3, UPCOMING_1, DUE, OVERDUE = "UPCOMING_3", "UPCOMING_1", "DUE", "OVERDUE"
_REMINDER_OFFSETS = ((0, DUE), (1, UPCOMING_1), (3, UPCOMING_3))  # strongest first

_MONTHS_AD = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


@dataclass(frozen=True)
class Obligation:
    calendar: str      # "AD" | "BS"
    period_key: str    # "2026-10" (AD) / "2083-07" (BS)
    label: str         # "October 2026" / "Kartik 2083 BS"
    start: date        # AD dates of the whole month
    end: date
    deadline: date     # = end


def ad_month(year: int, month: int) -> Obligation:
    last = _cal.monthrange(year, month)[1]
    return Obligation(
        AD, f"{year:04d}-{month:02d}", f"{_MONTHS_AD[month - 1]} {year}",
        date(year, month, 1), date(year, month, last), date(year, month, last),
    )


def bs_month(year: int, month: int) -> Obligation:
    start, end = bs_month_range_ad(year, month)
    return Obligation(
        BS, f"{year:04d}-{month:02d}", f"{BS_MONTHS[month]} {year} BS", start, end, end
    )


def obligations(today: date, lookback: int = 3) -> list[Obligation]:
    """This month and the previous `lookback` months of each calendar, newest
    first within a calendar. The BS list is empty if `today` is outside the
    years the BS tables cover."""
    out: list[Obligation] = []
    y, m = today.year, today.month
    for _ in range(lookback + 1):
        out.append(ad_month(y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    try:
        by, bm = to_bs(today).year, to_bs(today).month
    except (ValueError, OverflowError):
        return out
    for _ in range(lookback + 1):
        if not (BS_MIN_YEAR <= by <= BS_MAX_YEAR):
            break
        out.append(bs_month(by, bm))
        by, bm = bs_add_months(by, bm, -1)
    return out


def stage(ob: Obligation, today: date) -> Optional[str]:
    """The reminder that goes out on `today` for this obligation, or None.
    OVERDUE every working day after the deadline; before it, DUE / UPCOMING_1 /
    UPCOMING_3 on the working day at or before deadline, deadline-1, deadline-3."""
    if is_non_working_day(today):
        return None
    if today > ob.deadline:
        return OVERDUE
    for offset, name in _REMINDER_OFFSETS:
        if previous_working_day(ob.deadline - timedelta(days=offset)) == today:
            return name
    return None


def banner_status(ob: Obligation, today: date) -> Optional[str]:
    """What the always-visible banner says, regardless of working days:
    OVERDUE after the deadline, DUE on it, UPCOMING within 3 days, else None
    (too early to bother anyone)."""
    if today > ob.deadline:
        return OVERDUE
    if today == ob.deadline:
        return DUE
    if (ob.deadline - today).days <= 3:
        return "UPCOMING"
    return None


def days_overdue(ob: Obligation, today: date) -> int:
    return max(0, (today - ob.deadline).days)


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")


def _what(tasks: int, minutes: int) -> str:
    n = f"{tasks} task{'s' if tasks != 1 else ''} you submitted"
    return f"{n} ({fmt_minutes(minutes)})" if minutes else n


def reminder_message(ob: Obligation, st: str, tasks: int, minutes: int, today: date) -> str:
    # A Nepali label already ends in "BS"; an English one gets "(AD)" so the two are never confused.
    cal = f" ({ob.calendar})" if ob.calendar == "AD" else ""
    left = f"{_what(tasks, minutes)} in {ob.label}{cal} {'is' if tasks == 1 else 'are'} not yet sent to the HRMS"
    if st == OVERDUE:
        d = days_overdue(ob, today)
        return f"Timesheet overdue by {d} day{'s' if d != 1 else ''}: {left}."
    if st == DUE:
        return f"Timesheet due today ({ob.deadline.isoformat()}): {left}."
    days = (ob.deadline - today).days
    return f"Timesheet due in {days} day{'s' if days != 1 else ''} ({ob.deadline.isoformat()}): {left}."
