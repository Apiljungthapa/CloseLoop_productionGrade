"""Process-wide registry of the CONFIGURED holidays — days nobody works, on top
of the Saturday/Sunday weekend (`NEPAL_WEEKEND`).

This is a leaf module on purpose (no DB, no framework imports): the pure date
helpers in `nepali_calendar` (`is_non_working_day`, `next_working_day`,
`working_days_between`, ...) call into it without needing a session. It starts
EMPTY, so until a holiday is configured every helper behaves exactly as the
weekend-only rule always did.

It is filled from the `holidays` table by `services/holiday_service`: at
startup, after every write through `/api/holidays`, and on a timer (so a row
inserted straight into the database is picked up too). Whatever puts holidays
in — the admin page, a bulk POST, the seeded 2083 list, a future feed of a
public-holiday API — the rest of the app only ever reads them from here.

A holiday has a category (ALL, or GENDER / RELIGION for one group, naming
the `audience`, e.g. FEMALE / CHRISTIAN). Which categories are a day off for
EVERYONE's task dates is one decision, `SKIPPED_FOR_EVERYONE` below. Today it is
all of them: every holiday recorded in the table is skipped exactly like
Saturday/Sunday (due dates roll past it, Daily tasks skip it, the pickers refuse
and mark it), and several holidays on one date are just one non-working day.
Take GENDER / RELIGION out of that set and those holidays instead count only
when a caller asks about a specific `StaffProfile` (`is_holiday_for`).

The library's estimated festival dates are NOT here and are used nowhere in the
app: a day counts as a holiday only if it is recorded in the table.
"""

from __future__ import annotations

import datetime as _dt
from bisect import bisect_left, bisect_right
from typing import Container, Iterable, Mapping, NamedTuple, Optional

ALL = "ALL"
GENDER = "GENDER"
RELIGION = "RELIGION"
CATEGORIES = (ALL, GENDER, RELIGION)

# The categories that are a day off for everyone's task dates (the GLOBAL set).
# The owner's rule: every holiday recorded in the DB is skipped, whatever its
# category. Remove GENDER / RELIGION to make those apply to matching staff only.
SKIPPED_FOR_EVERYONE = frozenset(CATEGORIES)


class HolidayEntry(NamedTuple):
    day: _dt.date
    name: str
    category: str = ALL
    audience: Optional[str] = None  # who a GENDER / RELIGION holiday is for


class StaffProfile(NamedTuple):
    """What the holiday rules need to know about one person. Both optional: a
    value left None never matches a scoped holiday (nothing is assumed)."""

    gender: Optional[str] = None
    religion: Optional[str] = None


class _Snapshot(NamedTuple):
    names: Mapping[_dt.date, str]  # global (ALL) holidays: date -> name(s)
    days: tuple  # the same dates, sorted — for range counts
    scoped: Mapping[_dt.date, tuple]  # GENDER / RELIGION entries by date


# Replaced as ONE object, so a reader can never see the names of one load with
# the sorted dates of another.
_current = _Snapshot({}, (), {})


def holiday_skips_everyone(category: str) -> bool:
    """Is a holiday of this category a day off for everyone's task dates?"""
    return category in SKIPPED_FOR_EVERYONE


def set_holiday_entries(entries: Iterable[HolidayEntry]) -> None:
    """Replace every configured holiday. Entries whose category is skipped for
    everyone become the global set (names on the same date are joined, each
    once); any others are kept by date for `is_holiday_for`."""
    global _current
    names: dict = {}
    scoped: dict = {}
    for e in entries:
        if holiday_skips_everyone(e.category):
            seen = names.setdefault(e.day, [])
            if e.name not in seen:
                seen.append(e.name)
        else:
            scoped.setdefault(e.day, []).append(e)
    joined = {day: " / ".join(parts) for day, parts in names.items()}
    _current = _Snapshot(
        joined,
        tuple(sorted(joined)),
        {day: tuple(items) for day, items in scoped.items()},
    )


def set_configured_holidays(holidays: Mapping[_dt.date, str]) -> None:
    """Replace every configured holiday with these GLOBAL ones (date -> name)."""
    set_holiday_entries(HolidayEntry(day, name) for day, name in holidays.items())


def configured_holidays() -> dict:
    """A copy of the global holidays, date -> name."""
    return dict(_current.names)


def configured_holiday_name(day: _dt.date) -> str | None:
    return _current.names.get(day)


def is_configured_holiday(day: _dt.date) -> bool:
    """True for a GLOBAL holiday — one that moves everyone's task dates."""
    return day in _current.names


def scoped_holidays_on(day: _dt.date) -> tuple:
    """The GENDER / RELIGION holidays on ``day`` (empty tuple when none)."""
    return _current.scoped.get(day, ())


def _matches(entry: HolidayEntry, staff: StaffProfile) -> bool:
    """Does this scoped holiday apply to this person? Needs the person's
    matching attribute to be known AND equal to the holiday's audience."""
    if not entry.audience:
        return False
    wanted = entry.audience.strip().casefold()
    if entry.category == GENDER:
        return bool(staff.gender) and staff.gender.strip().casefold() == wanted
    if entry.category == RELIGION:
        return bool(staff.religion) and staff.religion.strip().casefold() == wanted
    return False


def is_holiday_for(day: _dt.date, staff: Optional[StaffProfile] = None) -> bool:
    """True when ``day`` is a holiday for this person: any global holiday, or a
    scoped one that applies to them. With no profile that is exactly
    `is_configured_holiday`."""
    if day in _current.names:
        return True
    if staff is None:
        return False
    return any(_matches(e, staff) for e in _current.scoped.get(day, ()))


def count_configured_holidays_between(
    start: _dt.date, end: _dt.date, *, exclude_weekdays: Container[int] = ()
) -> int:
    """Global holidays in ``(start, end]`` — after ``start``, up to and
    including ``end`` — that don't already fall on one of ``exclude_weekdays``
    (a holiday on a Saturday is not a second lost working day)."""
    days = _current.days
    if not days or end <= start:
        return 0
    lo, hi = bisect_right(days, start), bisect_right(days, end)
    return sum(1 for d in days[lo:hi] if d.weekday() not in exclude_weekdays)


def configured_holidays_in(start: _dt.date, end: _dt.date) -> dict:
    """Global holidays in ``[start, end]`` inclusive, date -> name."""
    days = _current.days
    lo, hi = bisect_left(days, start), bisect_right(days, end)
    return {d: _current.names[d] for d in days[lo:hi]}
