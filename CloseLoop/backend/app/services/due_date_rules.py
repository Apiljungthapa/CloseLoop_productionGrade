"""Centralized due-date rules — the single place that knows how far a
Task/SubTask due date is allowed to run.

A task's due date ALWAYS lies inside the period its plan belongs to: a Kartik
task is never due in Mangsir, and a date in another period shows up only in
that period's own plan. Nothing here ever extends or moves a period.

`due_date_type` / `post_period_due_days` ("N days after period end") are a
retired option. The columns and the AFTER_PERIOD_END enum value stay so old
rows and old clients still load, but they no longer widen anything: every
function below treats AFTER_PERIOD_END exactly like WITHIN_PERIOD, a
generated "after period end" date resolves to the period's last working day,
and the request schemas coerce the pair back to WITHIN_PERIOD / 0.

A sub-task obeys the same window, ADDITIONALLY capped so it can never be due
after its parent task: the maximum allowed sub-task date is always
`min(period_end, parent_task.due_date)`.

Every create/edit form, the recurring-generation engine, Close Pack
templates, and carry-forward all route through the functions here so the
rule can never drift out of sync between call sites.

This module ALSO holds the "N-Day Due Date" rule for RECURRING tasks/
sub-tasks (every frequency except "Once per Period" — Daily, Weekly,
Monthly, Quarterly, Semi-annual, Annual, and a Custom interval of any of
those): `due_date = occurrence_date + due_days`, recomputed fresh for
every occurrence — see `apply_due_days` below. The result is clamped to the
period the occurrence landed in, so an occurrence near the end of a period
becomes due on that period's last working day rather than spilling into the
next one.

Saturday and Sunday are weekends (`calendar.NEPAL_WEEKEND`) and the configured
holidays (`calendar.holiday_registry`) are closed days too — together the
NON-WORKING days (`calendar.is_non_working_day`). A GENERATED due date is never
left on one: `roll_to_working_day` moves it to a working day — see its
docstring for which direction and why a period boundary can force the other
one. Dates a user picks by hand are not rolled; the date pickers and
`validate_working_day` refuse a non-working day there instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from ..calendar import (
    NEPAL_WEEKEND,
    configured_holiday_name,
    is_non_working_day,
    next_working_day,
    previous_working_day,
)
from ..models import DueAnchor, DueDateType
from .recurrence import due_within


class DueDateOutOfRangeError(ValueError):
    """A due date fell outside the window its due_date_type/
    post_period_due_days allows. Raised by the validate_* functions below;
    routers catch it and turn it into a 400."""


class NonWorkingDayError(DueDateOutOfRangeError):
    """A due date a user picked lands on a Saturday, Sunday or holiday. A
    subclass of DueDateOutOfRangeError so every router that already turns that
    into a 400 handles this too."""


def validate_working_day(
    due_date: Optional[date],
    *,
    current: Optional[date] = None,
    label: str = "Task",
) -> None:
    """Raise NonWorkingDayError if `due_date` is a Saturday/Sunday
    (`calendar.NEPAL_WEEKEND`) or a configured holiday. The date pickers
    already grey those days out and refuse them; this is the server-side
    backstop for stale clients and the API.

    Only for a date a user EXPLICITLY supplied — never a computed fallback
    (e.g. a plan's due date, which is the period's last day and may be a
    weekend). `current` is the value already stored: re-submitting it
    unchanged is accepted, so editing an old task that predates this rule
    (or a holiday added after the date was picked, or any other field on
    it) never starts failing. None is always fine."""
    if due_date is None or due_date == current:
        return
    if due_date.weekday() in NEPAL_WEEKEND:
        raise NonWorkingDayError(
            f"{label} due date {due_date.isoformat()} falls on a "
            f"{due_date.strftime('%A')}. Saturday and Sunday are weekends — "
            f"pick a working day (Monday to Friday)"
        )
    holiday = configured_holiday_name(due_date)
    if holiday:
        raise NonWorkingDayError(
            f"{label} due date {due_date.isoformat()} falls on a holiday "
            f"({holiday}) — pick a working day"
        )


def _days(n: Optional[int]) -> int:
    """Normalize a possibly-None/negative post_period_due_days into a
    non-negative int — 0 (no extension) for anything else."""
    return n if (n and n > 0) else 0


def roll_to_working_day(
    d: date,
    *,
    lo: Optional[date] = None,
    hi: Optional[date] = None,
    prefer_later: bool = True,
) -> date:
    """`d` unchanged if it's a working day, else the nearest working day in
    the preferred direction (the following working day when `prefer_later` —
    Saturday/Sunday -> Monday, further if a holiday follows — the preceding
    one otherwise, Saturday/Sunday -> Friday).

    `lo` / `hi` are hard bounds the result must stay inside (a WITHIN_PERIOD
    due date can't pass period.end_date, an AFTER_PERIOD_END one can't pass
    its own ceiling). When the preferred day is out of bounds the OTHER
    direction is used; if neither fits (a window made only of non-working
    days) `d` is returned as-is rather than inventing a date outside the
    bounds."""
    if not is_non_working_day(d):
        return d
    later, earlier = next_working_day(d), previous_working_day(d)
    for candidate in ((later, earlier) if prefer_later else (earlier, later)):
        if (lo is None or candidate >= lo) and (hi is None or candidate <= hi):
            return candidate
    return d


def _counts_back_from_period_end(anchor: DueAnchor | str) -> bool:
    return getattr(anchor, "value", anchor) == "PERIOD_END"


def last_working_day(period_start: date, period_end: date) -> date:
    """The period's last working day: `period_end` itself, or the working day
    before it when the period ends on a Saturday/Sunday/holiday. Where a
    retired "N days after period end" task now lands, and the ceiling every
    rolled date is held to."""
    return roll_to_working_day(period_end, lo=period_start, hi=period_end, prefer_later=False)


def max_allowed_due_date(
    period_end: date,
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD,
    post_period_due_days: Optional[int] = 0,
) -> date:
    """The latest date a due_date may take in this period, i.e. the
    date-picker's max-date and the validator's ceiling: always
    `period.end_date`. `due_date_type` / `post_period_due_days` are accepted
    only so existing callers keep working — AFTER_PERIOD_END no longer pushes
    the ceiling past the period."""
    return period_end


def max_allowed_subtask_due_date(
    period_end: date,
    due_date_type: DueDateType,
    post_period_due_days: Optional[int],
    parent_task_due_date: Optional[date],
) -> date:
    """`min(period.end_date [+ post_period_due_days], parent_task.due_date)`
    — the sub-task's own window ceiling, additionally capped by its parent
    task's due date. Falls back to the sub-task's own ceiling alone when
    the parent has no due date at all."""
    ceiling = max_allowed_due_date(period_end, due_date_type, post_period_due_days)
    if parent_task_due_date is not None:
        return min(ceiling, parent_task_due_date)
    return ceiling


def validate_due_date(
    *,
    period_start: date,
    period_end: date,
    due_date: Optional[date],
    due_date_type: DueDateType,
    post_period_due_days: Optional[int],
    label: str = "Task",
) -> None:
    """Raise DueDateOutOfRangeError if `due_date` isn't allowed for this
    period + due_date_type + post_period_due_days. A None due_date is
    always fine (no deadline set). Never mutates or extends the period."""
    if due_date is None:
        return
    if due_date < period_start:
        raise DueDateOutOfRangeError(
            f"{label} due date {due_date.isoformat()} is before the period "
            f"start ({period_start.isoformat()})"
        )
    if due_date > period_end:
        raise DueDateOutOfRangeError(
            f"{label} due date {due_date.isoformat()} is after the period end "
            f"({period_end.isoformat()}). A due date must fall inside the "
            f"task's own period — a later date belongs to the next period."
        )


def validate_subtask_due_date(
    *,
    period_start: date,
    period_end: date,
    due_date: Optional[date],
    due_date_type: DueDateType,
    post_period_due_days: Optional[int],
    parent_task_due_date: Optional[date],
) -> None:
    """Same window check as `validate_due_date`, plus the sub-task-only
    rule: subtask.due_date <= parent_task.due_date."""
    validate_due_date(
        period_start=period_start,
        period_end=period_end,
        due_date=due_date,
        due_date_type=due_date_type,
        post_period_due_days=post_period_due_days,
        label="Sub-task",
    )
    if (
        due_date is not None
        and parent_task_due_date is not None
        and due_date > parent_task_due_date
    ):
        raise DueDateOutOfRangeError(
            f"Sub-task due date {due_date.isoformat()} cannot exceed parent "
            f"task due date {parent_task_due_date.isoformat()}"
        )


def resolve_generated_due_date(
    period_start: date,
    period_end: date,
    due_day_of_period: Optional[int],
    due_anchor: DueAnchor | str,
    due_date_type: DueDateType,
    post_period_due_days: Optional[int],
    *,
    roll_non_working: bool = True,
) -> date:
    """The due date a NON-recurring (once-per-period) template resolves to
    in one period. WITHIN_PERIOD keeps the existing "day N of the period"
    behavior (`due_within`, always clamped inside the period).
    AFTER_PERIOD_END ignores due_day_of_period/anchor — day N doesn't apply
    once we're past the period's own last day — and is always
    `period_end + post_period_due_days`, the type's own ceiling.

    A result that lands on a Saturday, Sunday or holiday is moved to a working
    day, never outside what the type allows (see `roll_to_working_day`):
      * WITHIN_PERIOD, anchor PERIOD_START -> the next working day; the
        previous one if that would pass period_end.
      * WITHIN_PERIOD, anchor PERIOD_END   -> the previous working day (it's
        "N days from close", so it must never slip closer to the close); the
        next one if that would pass period_start.
      * AFTER_PERIOD_END (retired "N days after period end") -> the period's
        last working day. It stays in its own period; the N is ignored.

    `roll_non_working=False` returns the raw, unrolled date. Only the
    "does this period already have its task" check needs that: tasks
    generated before non-working days were rolled sit on the raw date."""
    if due_date_type == DueDateType.AFTER_PERIOD_END:
        return last_working_day(period_start, period_end) if roll_non_working else period_end
    due = due_within(period_start, period_end, due_day_of_period, due_anchor)
    if not roll_non_working:
        return due
    return roll_to_working_day(
        due, lo=period_start, hi=period_end,
        prefer_later=not _counts_back_from_period_end(due_anchor),
    )


def resolve_generated_subtask_due_date(
    period_start: date,
    period_end: date,
    due_day_of_period: Optional[int],
    due_anchor: DueAnchor | str,
    due_date_type: DueDateType,
    post_period_due_days: Optional[int],
    parent_task_due_date: Optional[date],
) -> date:
    """The generated due date for a sub-task template, capped so it's
    never after its parent task's own due date — mirrors
    `resolve_generated_due_date` plus the sub-task-only min() rule."""
    d = resolve_generated_due_date(
        period_start, period_end, due_day_of_period, due_anchor,
        due_date_type, post_period_due_days,
    )
    if parent_task_due_date is not None:
        return min(d, parent_task_due_date)
    return d


@dataclass(frozen=True)
class DueDateWindow:
    """A period's allowed due-date window — what a date-picker/preview
    surfaces to describe the picker's bounds. `max_date` never passes
    `period_end`, so `is_after_period_end` is always False now; the field is
    kept for the clients that still read it."""

    period_start: date
    period_end: date
    min_date: date
    max_date: date
    is_after_period_end: bool

    @property
    def days_past_period_end(self) -> int:
        return max(0, (self.max_date - self.period_end).days)


def due_date_window(
    period_start: date,
    period_end: date,
    due_date_type: DueDateType,
    post_period_due_days: Optional[int],
    parent_task_due_date: Optional[date] = None,
) -> DueDateWindow:
    """Everything a date picker / preview needs to render the allowed
    range for one task or sub-task: min date (period start) and max date
    (period end, further capped by a parent task's due date for
    sub-tasks)."""
    if parent_task_due_date is not None:
        max_date = max_allowed_subtask_due_date(
            period_end, due_date_type, post_period_due_days, parent_task_due_date
        )
    else:
        max_date = max_allowed_due_date(period_end, due_date_type, post_period_due_days)
    return DueDateWindow(
        period_start=period_start,
        period_end=period_end,
        min_date=period_start,
        max_date=max_date,
        is_after_period_end=max_date > period_end,
    )


# ---------------------------------------------------------------------------
# N-Day Due Date (recurring tasks/sub-tasks — every frequency except "Once
# per Period"). See the module docstring.
# ---------------------------------------------------------------------------


def apply_due_days(
    occurrence_date: date,
    due_days: Optional[int],
    *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> date:
    """due_date for ONE recurring occurrence: occurrence_date + due_days,
    moved to the next working day if that is a Saturday, Sunday or holiday (a
    Monthly/Quarterly/Annual occurrence can land on one, and so can a
    Daily/Weekly one once due_days is added — Friday + 1 day, or a Weekly
    occurrence that falls on a holiday).
    due_days defaults to 0 (a negative value is also treated as 0) — due
    on the occurrence date itself, the behavior every recurring task/
    sub-task had before this field existed.

    Pass `period_start` / `period_end` (the period the occurrence landed in)
    to keep the result inside that period: the offset date is clamped to
    `period_end`, and a weekend/holiday roll that would cross it goes back to
    the previous working day instead — a Sunday occurrence on a period's last
    day is due the Friday before, not the Monday that opens the next period.
    Left out, nothing is bounded: that is for a "next occurrence" pointer,
    which names a date in a LATER period on purpose.

    Only the DUE date moves. The occurrence date itself — the idempotency
    key, the period an occurrence belongs to, a Daily title's date suffix —
    stays on the raw date."""
    due = occurrence_date + timedelta(days=_days(due_days))
    if period_end is None:
        return roll_to_working_day(due)
    return roll_to_working_day(min(due, period_end), lo=period_start, hi=period_end)


def cap_occurrence_search_end(range_end: date, due_days: Optional[int]) -> date:
    """When searching for which raw OCCURRENCE dates a recurring sub-task
    may land on under one parent occurrence, the search must be bounded by
    the LATEST occurrence date whose (occurrence + due_days) still lands
    on/before `range_end` (typically the parent task's own due date) — not
    by `range_end` itself, since `occurrence_dates()` only knows how to
    bound the raw occurrence date, never the offset due date. This is that
    bound: `range_end - due_days`."""
    return range_end - timedelta(days=_days(due_days))
