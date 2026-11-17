"""Automated timesheet: preview a month, then send it to the HRMS safely.

Flow (routers/timesheets.py is a thin wrapper around these functions):

  build_preview   read-only. Finds what the user SUBMITTED in the period (from the
                  tasks themselves - the time-log table is never read), matches each to
                  an HRMS activity, and folds in what is already on the HRMS timesheet.
  submit          re-computes the preview from scratch (the screen may be
                  stale), applies the user's choices, refuses duplicates, then
                  sends one entry at a time, committing after each, so a crash
                  halfway leaves a truthful record.
  verify_uncertain  after a dropped connection, look in the HRMS before
                  anything is sent again.

The hour maths, grouping and summaries are in timesheet_builder (pure); the
matching rules in timesheet_matching (pure); the HRMS calls behind
hrms_timesheet_gateway. This module is the database and the orchestration.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..calendar import classify_day, nepal_today
from ..config import settings
from ..models import (
    HrmsActivityMapping,
    SubTask,
    Task,
    TimesheetLine,
    TimesheetLineSource,
    TimesheetSubmission,
    User,
)
from . import hrms_service
from . import timesheet_builder as tb
from . import timesheet_events as ev
from .audit_service import log_action
from .hrms_timesheet_gateway import (
    ExistingEntry,
    HrmsTimesheetRejected,
    HrmsTimesheetUncertain,
    HrmsTimesheetUnavailable,
    ActivityEntry,
    NewDay,
    TimesheetGateway,
)
from .timesheet_matching import (
    MATCHED,
    Activity,
    mapping_keys,
    name_key,
)

logger = logging.getLogger(__name__)

TAKEN = ev.TAKEN
WORK_TYPES = tb.WORK_TYPES


class TimesheetError(Exception):
    """A request the timesheet refuses, with a code the UI can act on."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


# ---------------------------------------------------------------- dates


def month_bounds(month: str) -> tuple[date, date]:
    try:
        year, mon = (int(x) for x in month.split("-"))
        first = date(year, mon, 1)
    except (ValueError, AttributeError):
        raise TimesheetError(422, "BAD_MONTH", "Month must look like 2026-10")
    return first, date(year, mon, monthrange(year, mon)[1])


def resolve_range(month: str, through: Optional[date], today: Optional[date] = None):
    """(start, end) AD dates to cover. The whole month, or up to `through`
    (the BS-month-end case) when that falls inside it."""
    today = today or nepal_today()
    first, last = month_bounds(month)
    if first > today:
        raise TimesheetError(422, "FUTURE_MONTH", "That month has not started yet")
    end = last
    if through is not None:
        if through < first:
            raise TimesheetError(422, "BAD_THROUGH", "'Up to' date is before the month starts")
        end = min(through, last)
    return first, end


# ---------------------------------------------------------------- loading


async def _load_items(
    db: AsyncSession, tenant_id: str, task_ids: set[str], subtask_ids: set[str]
) -> dict[str, tb.Item]:
    """Item records for these tasks / sub tasks, plus the parent task of every sub task."""
    task_ids = set(task_ids)
    subs = {}
    if subtask_ids:
        subs = {
            s.id: s
            for s in (await db.execute(select(SubTask).where(SubTask.tenant_id == tenant_id, SubTask.id.in_(subtask_ids)))).scalars()
        }
        task_ids |= {s.task_id for s in subs.values()}
    tasks = {}
    if task_ids:
        tasks = {
            t.id: t
            for t in (await db.execute(select(Task).where(Task.tenant_id == tenant_id, Task.id.in_(task_ids)))).scalars()
        }
    items: dict[str, tb.Item] = {
        t.id: tb.Item(t.id, "task", t.title, t.description, t.source_template_id) for t in tasks.values()
    }
    for s in subs.values():
        items[s.id] = tb.Item(s.id, "subtask", s.title, s.description, s.source_template_id, s.task_id)
    return items


async def _load_mappings(
    db: AsyncSession, tenant_id: str, project_id: int, texts
) -> dict[tuple[str, str], int]:
    pairs = list({(k, v) for wt in texts for k, v, _ in mapping_keys(wt)})
    if not pairs:
        return {}
    rows = await db.execute(
        select(HrmsActivityMapping).where(
            HrmsActivityMapping.tenant_id == tenant_id,
            HrmsActivityMapping.hrms_project_id == project_id,
            tuple_(HrmsActivityMapping.key_kind, HrmsActivityMapping.key_value).in_(pairs),
        )
    )
    return {
        (m.key_kind, m.key_value): m.hrms_activity_id
        for m in rows.scalars()
    }


async def _sent_lines(
    db: AsyncSession, tenant_id: str, user_id: str, project_id: int, start: date, end: date
) -> list[ExistingEntry]:
    rows = await db.execute(
        select(TimesheetLine).where(
            TimesheetLine.tenant_id == tenant_id, TimesheetLine.user_id == user_id,
            TimesheetLine.hrms_project_id == project_id, TimesheetLine.status.in_(("SENT", "UNCERTAIN", "PENDING")),
            TimesheetLine.work_date >= start, TimesheetLine.work_date <= end,
        )
    )
    return [
        ExistingEntry(l.work_date, l.hrms_activity_id, float(l.hours), l.summary)
        for l in rows.scalars()
    ]


async def _unknown_lines(
    db: AsyncSession, tenant_id: str, user_id: str, project_id: int, start: date, end: date
) -> list[date]:
    """Days whose send ended with an unknown result (no answer / still being sent)."""
    rows = await db.execute(
        select(TimesheetLine.work_date).where(
            TimesheetLine.tenant_id == tenant_id, TimesheetLine.user_id == user_id,
            TimesheetLine.hrms_project_id == project_id, TimesheetLine.status.in_(("UNCERTAIN", "PENDING")),
            TimesheetLine.work_date >= start, TimesheetLine.work_date <= end,
        ).distinct()
    )
    return [r[0] for r in rows]


def _days(start: date, end: date) -> list[dict]:
    """Every date of the period, so the page can show the whole month."""
    out, d = [], start
    while d <= end:
        info = classify_day(d)
        out.append({"date": d, "non_working": not info.is_working_day,
                    "label": info.holiday or ("Weekend" if info.is_weekend else None)})
        d += timedelta(days=1)
    return out


@dataclass
class Computed:
    project_id: int
    project_name: Optional[str]
    month: str
    start: date
    end: date
    employee: Optional[dict] = None
    activities: list[Activity] = field(default_factory=list)
    lines: list[tb.Line] = field(default_factory=list)
    existing_hrms: list[ExistingEntry] = field(default_factory=list)
    sent_here: list[ExistingEntry] = field(default_factory=list)
    sent_here_unknown: list = field(default_factory=list)   # lines sent with an unknown result
    blockers: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    # Can the HRMS timesheet be read back? False for a POST-only HRMS.
    hrms_readable: bool = False
    # True when it can't, so duplicates are only checked against what CloseLoop sent.
    hrms_unchecked: bool = False
    # True when the project's member list doesn't show the user's e-mail (a warning only).
    hrms_unlisted: bool = False


def _issue(code: str, message: str) -> dict:
    return {"code": code, "message": message}


async def compute(
    db: AsyncSession,
    user: User,
    *,
    project_id: int,
    month: str,
    start: date,
    end: date,
    hrms_activities: list[dict],
    hrms_project: Optional[dict],
    gateway: TimesheetGateway,
) -> Computed:
    c = Computed(project_id, (hrms_project or {}).get("name"), month, start, end)
    c.activities = [Activity.from_hrms(a) for a in hrms_activities]
    c.hrms_readable = gateway.is_available() and gateway.can_list()

    if hrms_project is None:
        c.blockers.append(_issue("PROJECT_NOT_FOUND", "That project is not in the HRMS"))
    else:
        # The HRMS identifies the employee by the e-mail we send, and checks it itself.
        # This is only an early heads-up when the project's member list doesn't show it.
        c.employee = hrms_service.find_employee(hrms_project, user.email)
        if c.employee is None:
            c.hrms_unlisted = True
            c.warnings.append(_issue(
                "NOT_A_PROJECT_MEMBER",
                f"Your e-mail ({user.email}) is not listed on this HRMS project. The HRMS decides, "
                "and it may refuse the timesheet if you are not set up there.",
            ))

    # What the user SUBMITTED in the period (their tasks, sub tasks and own part of group
    # tasks), from the tasks themselves. The time-log table is never read. Anything already
    # filed is set aside so it can't be sent twice.
    todo, filed = await ev.unfiled_work(db, start, end, tenant_id=user.tenant_id, user_id=user.id)
    work = [tb.Work(e.unit_id, e.day, e.minutes) for e in todo]
    items = await _load_items(
        db, user.tenant_id,
        {e.unit_id for e in todo if e.kind == "task"} | {e.parent_id for e in todo if e.parent_id},
        {e.unit_id for e in todo if e.kind == "subtask"},
    )
    mappings = await _load_mappings(db, user.tenant_id, project_id, tb.work_texts(work, items))
    c.lines = tb.build_lines(
        work, items, c.activities, mappings, max_summary_chars=settings.TIMESHEET_SUMMARY_MAX_CHARS
    )

    if not gateway.is_available():
        c.blockers.append(_issue(
            "HRMS_NOT_CONNECTED",
            "The HRMS timesheet API is not connected, so nothing can be sent yet.",
        ))
    else:
        if gateway.can_list():
            try:
                c.existing_hrms = await run_in_threadpool(
                    gateway.list_entries, email=user.email, project_id=project_id,
                    start=start, end=end,
                )
            except HrmsTimesheetUnavailable:
                # It CAN be read, it just failed right now: don't guess, wait.
                c.blockers.append(_issue(
                    "HRMS_ENTRIES_UNAVAILABLE",
                    "Your existing HRMS timesheet can't be read right now, so duplicates can't be ruled "
                    "out. Nothing can be sent until it can.",
                ))
        else:
            c.hrms_unchecked = True
            c.warnings.append(_issue(
                "HRMS_ENTRIES_NOT_CHECKED",
                "CloseLoop can't read your HRMS timesheet, so it only stops you re-sending what CloseLoop "
                "itself sent. Entries added directly in the HRMS are not detected - check there first.",
            ))
    c.sent_here = await _sent_lines(db, user.tenant_id, user.id, project_id, start, end)
    c.sent_here_unknown = await _unknown_lines(db, user.tenant_id, user.id, project_id, start, end)
    # UNCERTAIN / PENDING lines count as "sent" here on purpose: until verified, assume they
    # may be on the HRMS and don't offer the same slot again.
    tb.apply_existing(c.lines, c.existing_hrms, c.sent_here)

    in_flight = sum(1 for e in c.sent_here_unknown)
    if in_flight:
        c.warnings.append(_issue(
            "NEEDS_VERIFY",
            f"{in_flight} day{'s were' if in_flight != 1 else ' was'} sent with a result that is unknown. "
            "Settle that submission (below) before sending anything else for it.",
        ))
    missing = sum(1 for l in c.lines if l.needs_hours)
    if missing:
        c.warnings.append(_issue(
            "HOURS_MISSING",
            f"{missing} submitted task{'s have' if missing != 1 else ' has'} no Time Taken, so the hours "
            f"{'are' if missing != 1 else 'is'} empty - type what you worked, or leave "
            f"{'them' if missing != 1 else 'it'} out.",
        ))
    if not c.lines and not filed:
        c.warnings.append(_issue(
            "NOTHING_SUBMITTED",
            "Nothing to fill in for this period: you have not submitted any task or sub-task in it.",
        ))

    c.totals = tb.totals(c.lines, len(filed), sum(e.minutes for e in filed))
    if not settings.TIMESHEET_SUBMIT_ENABLED:
        c.blockers.append(_issue("SUBMIT_DISABLED", _SUBMIT_OFF))
    return c


_SUBMIT_OFF = "Sending to the HRMS is switched off. You can review the timesheet, but not submit it yet."


def preview_dict(c: Computed) -> dict:
    existing = [
        {"work_date": e.work_date, "hrms_activity_id": e.activity_id,
         "hours": e.hours, "origin": o}
        for rows, o in ((c.existing_hrms, "HRMS"), (c.sent_here, "CLOSELOOP")) for e in rows
    ]
    return {
        "hrms_project_id": c.project_id,
        "hrms_project_name": c.project_name,
        "month": c.month,
        "range_start": c.start,
        "range_end": c.end,
        "employee_id": (c.employee or {}).get("id"),
        "submit_enabled": settings.TIMESHEET_SUBMIT_ENABLED,
        "can_submit": not c.blockers,
        "hrms_entries_readable": c.hrms_readable,
        "lines": [_line_dict(l) for l in c.lines],
        "existing_entries": existing,
        "days": _days(c.start, c.end),
        "blockers": c.blockers,
        "warnings": c.warnings,
        "totals": c.totals,
    }


def _line_dict(l: tb.Line) -> dict:
    return {
        "key": l.key, "work_date": l.work_date, "minutes": l.minutes, "hours": l.hours,
        "status": l.status, "tier": l.tier, "score": l.score, "reason": l.reason,
        "hrms_activity_id": l.activity_id, "sub_values": dict(l.sub_values),
        "suggestions": [
            {"activity_id": s.activity_id, "score": s.score, "reason": s.reason}
            for s in l.suggestions
        ],
        "summary": l.summary, "work_type": l.work_type,
        "sources": [
            {"kind": s.unit_kind, "unit_id": s.unit_id, "minutes": s.minutes,
             "task_title": s.task_title, "subtask_title": s.subtask_title}
            for s in l.sources
        ],
        "flags": l.flags, "selected": l.selected, "existing_hours": l.existing_hours,
        "needs_hours": l.needs_hours,
    }


async def build_preview(db: AsyncSession, user: User, **kw) -> dict:
    return preview_dict(await compute(db, user, **kw))


# ---------------------------------------------------------------- submit


MAX_VALUE_CHARS = 500   # one sub-activity value


def _validate_choice(choice, by_id: dict[int, Activity]) -> tuple[int, dict[int, str]]:
    """The activity and the sub-activity values the user chose: every sub activity
    of the activity needs a value, and no value may belong to another activity."""
    act = by_id.get(choice.hrms_activity_id)
    if act is None:
        raise TimesheetError(422, "UNKNOWN_ACTIVITY", "That activity is not in the HRMS activity list")
    values = {int(k): " ".join((v or "").split()) for k, v in (choice.sub_values or {}).items()}
    wanted = {s.id for s in act.subs}
    if set(values) - wanted:
        raise TimesheetError(422, "UNKNOWN_SUB_ACTIVITY", f"A value was given for a field that '{act.name}' does not have")
    if not wanted:
        return act.id, {}
    missing = [s.name for s in act.subs if not values.get(s.id)]
    if missing:
        raise TimesheetError(
            422, "SUB_VALUES_REQUIRED", f"'{act.name}' needs a value for: {', '.join(missing)}"
        )
    if any(len(v) > MAX_VALUE_CHARS for v in values.values()):
        raise TimesheetError(422, "VALUE_TOO_LONG", f"A value is longer than {MAX_VALUE_CHARS} characters")
    return act.id, values


async def submit(
    db: AsyncSession,
    user: User,
    *,
    project_id: int,
    month: str,
    start: date,
    end: date,
    choices: Sequence,
    idempotency_key: str,
    acknowledge_warnings: bool,
    hrms_activities: list[dict],
    hrms_project: Optional[dict],
    gateway: TimesheetGateway,
) -> TimesheetSubmission:
    if not settings.TIMESHEET_SUBMIT_ENABLED:
        raise TimesheetError(409, "SUBMIT_DISABLED", _SUBMIT_OFF)

    # Plain values: a rollback below expires `user`, and reading an expired ORM
    # object inside async code raises MissingGreenlet.
    tenant_id, user_id, email = user.tenant_id, user.id, user.email

    # A repeated request (double click, retry after a timeout) returns the first result.
    prior = await _submission_by_key(db, tenant_id, user_id, idempotency_key)
    if prior is not None:
        return prior

    c = await compute(
        db, user, project_id=project_id, month=month, start=start, end=end,
        hrms_activities=hrms_activities, hrms_project=hrms_project, gateway=gateway,
    )
    if c.blockers:
        raise TimesheetError(409, c.blockers[0]["code"], c.blockers[0]["message"])

    by_key = {l.key: l for l in c.lines}
    by_id = {a.id: a for a in c.activities}
    if len({ch.key for ch in choices}) != len(choices):
        raise TimesheetError(422, "DUPLICATE_LINE", "The same line was sent twice")

    chosen: list[tb.Chosen] = []
    needs_ack = False
    for ch in choices:
        base = by_key.get(ch.key)
        if base is None:
            raise TimesheetError(
                409, "STALE_PREVIEW",
                "Your time logs or the HRMS changed since this screen loaded. Reload it and review again.",
            )
        if any(f in tb._BLOCKING_FLAGS for f in base.flags):
            raise TimesheetError(409, "LINE_HELD", "A selected line is already on the timesheet or can't be sent")
        act_id, values = _validate_choice(ch, by_id)
        if ch.work_type not in WORK_TYPES:
            raise TimesheetError(422, "BAD_WORK_TYPE", "Unknown work type")
        summary = " ".join((ch.summary or base.summary).split())
        if not summary:
            raise TimesheetError(422, "SUMMARY_REQUIRED", "Every entry needs a summary")
        if len(summary) > settings.TIMESHEET_SUMMARY_MAX_CHARS:
            raise TimesheetError(
                422, "SUMMARY_TOO_LONG",
                f"A summary is longer than {settings.TIMESHEET_SUMMARY_MAX_CHARS} characters",
            )
        needs_ack |= bool(base.flags) or base.status != MATCHED or base.needs_hours
        if base.needs_hours:
            # No Time Taken was entered at submission: the hours are what the user typed - nothing invented.
            if not ch.minutes:
                raise TimesheetError(
                    422, "HOURS_REQUIRED",
                    f"Enter the hours you worked on {base.sources[0].subtask_title or base.sources[0].task_title}",
                )
            minutes, sources = ch.minutes, [replace(src, minutes=ch.minutes) for src in base.sources]
        else:
            if ch.minutes is not None and ch.minutes != base.minutes:
                raise TimesheetError(
                    422, "HOURS_FIXED",
                    "These hours are the Time Taken you entered when you submitted, and can't be changed here.",
                )
            minutes, sources = base.minutes, list(base.sources)
        chosen.append(tb.Chosen(
            base.work_date, act_id, minutes, summary, ch.work_type, sources, values,
            base.tier, base.score, base.reason, list(base.units),
        ))
    needs_ack |= c.hrms_unchecked or c.hrms_unlisted
    if needs_ack and not acknowledge_warnings:
        raise TimesheetError(
            409, "ACK_REQUIRED",
            "Some lines carry warnings or were chosen by hand, or your HRMS timesheet could not be checked. "
            "Confirm you reviewed them.",
        )

    try:
        merged = tb.merge_chosen(chosen, settings.TIMESHEET_SUMMARY_MAX_CHARS)
    except tb.MergeConflict as exc:
        raise TimesheetError(422, exc.code, exc.message)
    dupes = tb.find_duplicates(merged, c.existing_hrms, c.sent_here)
    if dupes:
        d = dupes[0]
        if d.work_date in {e.work_date for e in c.sent_here}:
            raise TimesheetError(
                409, "DAY_ALREADY_SENT",
                f"{d.work_date.isoformat()} was already sent to the HRMS. A day is sent once - add anything "
                "extra for it in the HRMS directly.",
            )
        raise TimesheetError(409, "DUPLICATE_ENTRY",
                             f"{d.work_date.isoformat()} already has an entry for that activity on the timesheet")
    _check_day_totals(merged, c)

    submission = TimesheetSubmission(
        tenant_id=tenant_id, user_id=user_id, hrms_project_id=project_id,
        hrms_project_name=c.project_name or "", hrms_email=email,
        hrms_employee_id=(c.employee or {}).get("id"),
        range_start=start, range_end=end, idempotency_key=idempotency_key, status="SUBMITTING",
    )
    for m in merged:
        line = TimesheetLine(
            tenant_id=tenant_id, user_id=user_id, hrms_project_id=project_id,
            work_date=m.work_date, hrms_activity_id=m.activity_id, hrms_sub_activity_id=0,
            sub_values={str(k): v for k, v in m.sub_values.items()} or None,
            minutes=m.minutes, hours=m.hours, summary=m.summary, work_type=m.work_type,
            match_tier=m.tier, match_score=m.score, match_reason=(m.reason or "")[:300], status="PENDING",
        )
        line.sources = [_source_row(user_id, s) for s in m.sources]
        submission.lines.append(line)
    db.add(submission)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        again = await _submission_by_key(db, tenant_id, user_id, idempotency_key)
        if again is not None:
            return again
        raise TimesheetError(409, "ALREADY_SUBMITTING", "Some of these time entries are already being submitted")

    await _remember_mappings(db, user, project_id, merged)
    await log_action(
        db, tenant_id=tenant_id, user_id=user_id, action="TIMESHEET_SUBMIT_START",
        entity_type="timesheet_submission", entity_id=submission.id,
        meta={"project": project_id, "lines": len(merged), "minutes": sum(m.minutes for m in merged)},
    )
    await db.commit()   # the intent is on record BEFORE anything leaves the building

    by_day: dict[date, list[TimesheetLine]] = {}
    for line in submission.lines:
        by_day.setdefault(line.work_date, []).append(line)
    for day_lines in by_day.values():
        await _send_day(db, gateway, email, project_id, day_lines)
    _finish(submission)
    await log_action(
        db, tenant_id=tenant_id, user_id=user_id, action="TIMESHEET_SUBMIT_DONE",
        entity_type="timesheet_submission", entity_id=submission.id,
        meta={"status": submission.status, "statuses": [l.status for l in submission.lines]},
    )
    await db.commit()
    return submission


def _check_day_totals(merged: Sequence[tb.Chosen], c: Computed) -> None:
    per_day: dict[date, float] = {}
    for e in list(c.existing_hrms) + list(c.sent_here):
        per_day[e.work_date] = per_day.get(e.work_date, 0.0) + e.hours
    for m in merged:
        per_day[m.work_date] = per_day.get(m.work_date, 0.0) + m.hours
    over = next((d for d, h in sorted(per_day.items()) if h > 24.0001), None)
    if over:
        raise TimesheetError(409, "DAY_OVER_24H", f"{over.isoformat()} would total more than 24 hours")


def _source_row(user_id: str, s: tb.Source) -> TimesheetLineSource:
    """The database record that this submission is spoken for - one per person, so the
    same task / sub task can never be filed twice by the same person."""
    return TimesheetLineSource(
        user_id=user_id,
        task_id=s.unit_id if s.unit_kind == "task" else None,
        subtask_id=s.unit_id if s.unit_kind == "subtask" else None,
        minutes=s.minutes,
    )


def day_summary(lines: Sequence[TimesheetLine]) -> str:
    """The HRMS keeps ONE summary per day, so a day's line summaries are joined
    (repeats dropped) and kept within the length limit."""
    seen: set[str] = set()
    parts = [p for l in lines for p in (l.summary or "").split(" | ") if p and not (p in seen or seen.add(p))]
    return tb.clip(" | ".join(parts), settings.TIMESHEET_SUMMARY_MAX_CHARS)[0]


async def _send_day(
    db: AsyncSession, gateway: TimesheetGateway, email: str, project_id: int, lines: Sequence[TimesheetLine]
) -> None:
    """One HRMS request for one date; all of the date's lines share its fate."""
    day = NewDay(
        email=email, project_id=project_id, work_date=lines[0].work_date, summary=day_summary(lines),
        entries=tuple(
            ActivityEntry(
                activity_id=l.hrms_activity_id, hours=float(l.hours), work_type=l.work_type,
                sub_values={int(k): v for k, v in (l.sub_values or {}).items()},
            )
            for l in lines
        ),
    )
    try:
        ext = await run_in_threadpool(gateway.create_day, day)
        for l in lines:
            l.status, l.hrms_entry_id = "SENT", (str(ext) if ext is not None else None)
            l.sent_at = datetime.now(timezone.utc)
    except (HrmsTimesheetRejected, HrmsTimesheetUnavailable) as exc:
        # Refused, or never sent: definitely not on the HRMS, so free the time
        # entries to be sent again from the screen.
        for l in lines:
            l.status, l.error = "FAILED", str(exc)[:500]
            await _release_sources(db, l)
    except HrmsTimesheetUncertain as exc:
        for l in lines:
            l.status, l.error = "UNCERTAIN", str(exc)[:500]
    except Exception as exc:  # noqa: BLE001 - any other failure: we can't know
        logger.exception("Unexpected error sending timesheet day %s", lines[0].work_date)
        for l in lines:
            l.status, l.error = "UNCERTAIN", f"Unexpected error: {type(exc).__name__}"
    await db.commit()


async def _release_sources(db: AsyncSession, line: TimesheetLine) -> None:
    """Let go of the time entries a line was holding. A statement, not the
    `sources` relationship: that may not be loaded, and a lazy load is not
    allowed in async code."""
    await db.execute(delete(TimesheetLineSource).where(TimesheetLineSource.line_id == line.id))
    db.expire(line, ["sources"])


def _finish(sub: TimesheetSubmission) -> None:
    states = {l.status for l in sub.lines}
    if states == {"SENT"}:
        sub.status = "SUBMITTED"
    elif "SENT" not in states and states <= {"FAILED"}:
        sub.status = "FAILED"
    else:
        sub.status = "PARTIAL"
    sub.completed_at = datetime.now(timezone.utc)


async def _remember_mappings(db: AsyncSession, user: User, project_id: int, merged: Sequence[tb.Chosen]) -> None:
    """Remember what was confirmed, so next month's same tasks match by id."""
    seen: set[tuple[str, str]] = set()
    for m in merged:
        for wt in m.units:
            kind, value = ("template", wt.template_id) if wt.template_id else ("title", name_key(wt.title))
            if not value or (kind, value) in seen:
                continue
            seen.add((kind, value))
            stmt = pg_insert(HrmsActivityMapping).values(
                tenant_id=user.tenant_id, hrms_project_id=project_id, key_kind=kind, key_value=value,
                hrms_activity_id=m.activity_id, hrms_sub_activity_id=0,
                confirmed_count=1, confirmed_by_id=user.id,
            )
            await db.execute(stmt.on_conflict_do_update(
                constraint="uq_hrms_activity_mapping",
                set_={
                    "hrms_activity_id": stmt.excluded.hrms_activity_id,
                    "confirmed_count": HrmsActivityMapping.confirmed_count + 1,
                    "confirmed_by_id": user.id,
                },
            ))


async def _submission_by_key(
    db: AsyncSession, tenant_id: str, user_id: str, key: str
) -> Optional[TimesheetSubmission]:
    row = await db.execute(
        select(TimesheetSubmission).where(
            TimesheetSubmission.tenant_id == tenant_id, TimesheetSubmission.user_id == user_id,
            TimesheetSubmission.idempotency_key == key,
        )
    )
    sub = row.scalar_one_or_none()
    if sub is not None:
        await db.refresh(sub, ["lines"])
    return sub


# ---------------------------------------------------------------- verify / history


async def get_submission(db: AsyncSession, user: User, submission_id: str) -> TimesheetSubmission:
    sub = (await db.execute(
        select(TimesheetSubmission).where(
            TimesheetSubmission.id == submission_id, TimesheetSubmission.tenant_id == user.tenant_id,
            TimesheetSubmission.user_id == user.id,
        )
    )).scalar_one_or_none()
    if sub is None:
        raise TimesheetError(404, "NOT_FOUND", "Submission not found")
    await db.refresh(sub, ["lines"])
    return sub


async def list_submissions(
    db: AsyncSession, user: User, project_id: Optional[int], start: Optional[date], end: Optional[date]
) -> list[TimesheetSubmission]:
    q = select(TimesheetSubmission).where(
        TimesheetSubmission.tenant_id == user.tenant_id, TimesheetSubmission.user_id == user.id
    )
    if project_id is not None:
        q = q.where(TimesheetSubmission.hrms_project_id == project_id)
    if start is not None:
        q = q.where(TimesheetSubmission.range_end >= start)
    if end is not None:
        q = q.where(TimesheetSubmission.range_start <= end)
    subs = list((await db.execute(q.order_by(TimesheetSubmission.created_at.desc()).limit(50))).scalars())
    for s in subs:
        await db.refresh(s, ["lines"])
    return subs


async def verify_uncertain(
    db: AsyncSession, user: User, submission_id: str, gateway: TimesheetGateway
) -> TimesheetSubmission:
    """For lines whose outcome is unknown: look in the HRMS. Found -> SENT. Not
    found -> FAILED and its time entries are freed to send again. If the HRMS
    can't be read, nothing changes."""
    sub = await get_submission(db, user, submission_id)
    unknown = [l for l in sub.lines if l.status in ("UNCERTAIN", "PENDING")]
    if not unknown:
        return sub
    if not (gateway.is_available() and gateway.can_list()):
        raise TimesheetError(
            409, "CANNOT_VERIFY",
            "CloseLoop can't read the HRMS timesheet, so it can't check. Look in the HRMS yourself, then "
            "mark the entries as sent or not sent.",
        )
    try:
        existing = await run_in_threadpool(
            gateway.list_entries, email=sub.hrms_email or "", project_id=sub.hrms_project_id,
            start=min(l.work_date for l in unknown), end=max(l.work_date for l in unknown),
        )
    except HrmsTimesheetUnavailable:
        raise TimesheetError(503, "HRMS_ENTRIES_UNAVAILABLE", "The HRMS timesheet can't be read right now. Nothing was changed.")
    on_hrms = {(e.work_date, e.activity_id) for e in existing}
    for line in unknown:
        if (line.work_date, line.hrms_activity_id) in on_hrms:
            line.status, line.error = "SENT", None
            line.sent_at = line.sent_at or datetime.now(timezone.utc)
        else:
            line.status, line.error = "FAILED", "Not found in the HRMS after the connection dropped"
            await _release_sources(db, line)
    _finish(sub)
    await log_action(
        db, tenant_id=user.tenant_id, user_id=user.id, action="TIMESHEET_VERIFY",
        entity_type="timesheet_submission", entity_id=sub.id,
        meta={"statuses": [l.status for l in sub.lines]},
    )
    await db.commit()
    return sub


async def resolve_uncertain(
    db: AsyncSession, user: User, submission_id: str, outcome: str
) -> TimesheetSubmission:
    """The user looked in the HRMS themselves (CloseLoop can't read it) and says
    whether the unknown entries arrived. SENT keeps them as sent; NOT_SENT frees
    their time entries to be sent again - which duplicates them in the HRMS if
    the user was wrong, so the screen asks for confirmation first."""
    if outcome not in ("SENT", "NOT_SENT"):
        raise TimesheetError(422, "BAD_OUTCOME", "Outcome must be SENT or NOT_SENT")
    sub = await get_submission(db, user, submission_id)
    unknown = [l for l in sub.lines if l.status in ("UNCERTAIN", "PENDING")]
    if not unknown:
        return sub
    for line in unknown:
        if outcome == "SENT":
            line.status, line.error = "SENT", None
            line.sent_at = line.sent_at or datetime.now(timezone.utc)
        else:
            line.status, line.error = "FAILED", "Marked as not sent after checking the HRMS"
            await _release_sources(db, line)
    _finish(sub)
    await log_action(
        db, tenant_id=user.tenant_id, user_id=user.id, action="TIMESHEET_RESOLVE",
        entity_type="timesheet_submission", entity_id=sub.id,
        meta={"outcome": outcome, "lines": len(unknown)},
    )
    await db.commit()
    return sub
