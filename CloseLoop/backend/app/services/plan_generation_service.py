"""
Plan generation service.

`generate_plans(pack, start_period, entities)` materializes the pack's tasks
into the tenant's periods, from `start_period` forward across a rolling
horizon.

  * A NON-recurring template (frequency = NULL) -> one Task in every period,
    due on "day N of the period" (due_day_of_period).

  * A RECURRING template -> occurrence dates are computed forward from the
    template's `recurrence_start` (see services/recurrence.py); each
    occurrence is dropped into whichever period contains it, as a Task due
    on that exact date. DAILY / WEEKLY land many times per period;
    MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL land once every 1 / 3 / 6 /
    12 months.

A Task in the running period (or a past one) is NOT_STARTED; a Task in a
future period is UPCOMING — visible to the Lead but not actionable until
that period is current. Generation is idempotent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import List, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    CalendarType,
    ClosePack,
    DueDateType,
    Entity,
    Period,
    PeriodStatus,
    Plan,
    PlanStatus,
    Priority,
    RecurrenceFrequency,
    Role,
    SubTask,
    Task,
    TaskEntityAssignee,
    TaskStatus,
    User,
)
from .audit_service import log_action
from .close_pack_service import snapshot_pack
from ..calendar import BS_MONTHS, nepal_today, to_bs
from .recurrence import SUBPERIOD, occurrence_dates
from .due_date_rules import (
    apply_due_days,
    cap_occurrence_search_end,
    resolve_generated_due_date,
    resolve_generated_subtask_due_date,
)

# How many periods a "Generate plans" / period-close top-up materialises at
# once. 1 = ONLY the currently-running period; the next period's plans are
# generated when that period closes (by the scheduler — see
# carry_forward_service). Nothing is pre-generated / locked ahead, so the
# dashboard only ever shows the live period, plans are always cut from the
# LATEST pack version, and the pack->plan sync only touches one plan.
DEFAULT_HORIZON = 1


def _date_suffix(d: date, calendar: CalendarType) -> str:
    """The short date suffix a sub-period (DAILY/WEEKLY) recurring task or
    sub-task's title gets per occurrence — e.g. "Trade Receivables — Aug 17"
    for an AD pack, "Trade Receivables — Bhadra 17" for a BS (Nepali) one.
    Always in the Close Pack's OWN calendar_type, never hardcoded to AD, so
    a BS pack's task names read in Nepali dates like every other BS-calendar
    date shown for that pack."""
    if calendar == CalendarType.BS:
        bs = to_bs(d)
        return f"{BS_MONTHS[bs.month]} {bs.day}"
    return f"{d:%b %d}"


def _tmpl_time(raw) -> Optional[time]:
    if raw is None or isinstance(raw, time):
        return raw
    return time.fromisoformat(raw)


def _as_date(raw) -> Optional[date]:
    if raw is None or isinstance(raw, date):
        return raw
    return date.fromisoformat(raw)


def _freq(raw) -> Optional[RecurrenceFrequency]:
    if raw in (None, ""):
        return None
    if isinstance(raw, RecurrenceFrequency):
        return raw
    return RecurrenceFrequency(raw)


def _due_type(raw) -> DueDateType:
    """The template's due_date_type, for RESOLVING a due date only: a legacy
    AFTER_PERIOD_END template still maps to the period's last working day (see
    due_date_rules.resolve_generated_due_date). Rows never STORE it — the
    option is retired, so generated rows are always WITHIN_PERIOD / 0 days."""
    if raw in (None, ""):
        return DueDateType.WITHIN_PERIOD
    if isinstance(raw, DueDateType):
        return raw
    return DueDateType(raw)


def _post_period_days(tmpl) -> int:
    raw = tmpl.get("post_period_due_days") if isinstance(tmpl, dict) else getattr(tmpl, "post_period_due_days", None)
    return raw if (raw and raw > 0) else 0


def _due_days(tmpl) -> int:
    """N-Day Due Date — days after a RECURRING occurrence's own landing
    date it becomes due (see services/due_date_rules.apply_due_days).
    Meaningless (and ignored) when the item doesn't recur."""
    raw = tmpl.get("due_days") if isinstance(tmpl, dict) else getattr(tmpl, "due_days", None)
    return raw if (raw and raw > 0) else 0


def _tmpl_priority(raw) -> Priority:
    if raw is None:
        return Priority.MEDIUM
    return raw if isinstance(raw, Priority) else Priority(raw)


def _sub_priority(s_tmpl) -> Priority:
    """A sub-task's own priority: a dict (pack/custom-rule sub-task) or a
    bare TaskSubtaskInline object (freshly-submitted custom sub-task)."""
    raw = s_tmpl.get("priority") if isinstance(s_tmpl, dict) else getattr(s_tmpl, "priority", None)
    return _tmpl_priority(raw)


def _sub_tags(s_tmpl) -> list[str]:
    raw = s_tmpl.get("tags") if isinstance(s_tmpl, dict) else getattr(s_tmpl, "tags", None)
    return list(raw) if raw else []


async def _ordered_window(db, tenant_id, start_period, horizon) -> list[Period]:
    rows = await db.execute(
        select(Period)
        .where(Period.tenant_id == tenant_id, Period.start_date >= start_period.start_date)
        .order_by(Period.start_date)
        .limit(horizon)
    )
    return list(rows.scalars().all())


async def _current_period_end(db, tenant_id, fallback: Period) -> date:
    today = nepal_today()
    row = await db.execute(
        select(Period)
        .where(Period.tenant_id == tenant_id, Period.start_date <= today, Period.end_date >= today)
        .order_by(Period.start_date.desc())
        .limit(1)
    )
    p = row.scalar_one_or_none()
    if p:
        return p.end_date
    row = await db.execute(
        select(Period)
        .where(
            Period.tenant_id == tenant_id,
            Period.status.in_([PeriodStatus.OPEN, PeriodStatus.IN_PROGRESS]),
        )
        .order_by(Period.start_date)
        .limit(1)
    )
    p = row.scalar_one_or_none()
    return p.end_date if p else fallback.start_date - timedelta(days=1)


async def _get_or_create_plan(
    db, *, tenant_id, period, entity_id, owner_id, pack=None, version_number=None
) -> Plan:
    q = select(Plan).where(
        Plan.tenant_id == tenant_id,
        Plan.period_id == period.id,
        Plan.entity_id == entity_id,
    )
    if pack is not None:
        q = q.where(Plan.source_pack_id == pack.id)
    else:
        q = q.order_by(Plan.source_pack_id.is_(None))  # prefer a pack plan
    existing = (await db.execute(q)).scalars().first()
    if existing:
        if owner_id and not existing.owner_id:
            existing.owner_id = owner_id
        return existing
    plan = Plan(
        tenant_id=tenant_id,
        period_id=period.id,
        entity_id=entity_id,
        owner_id=owner_id,
        source_pack_id=pack.id if pack else None,
        source_pack_version=version_number,
        title=(f"{pack.name} — {period.label}" if pack else f"Ad-hoc tasks — {period.label}"),
        description=pack.description if pack else None,
        status=PlanStatus.OPEN,
        due_date=period.end_date,
    )
    db.add(plan)
    await db.flush()
    return plan


async def _existing_keys(db, plan_id: str) -> set[tuple]:
    """Idempotency keys for "does this occurrence already exist" — keyed by
    OCCURRENCE date (coalesced to due_date for a non-recurring task, which
    has no separate occurrence_date), never by due_date directly: due_date
    can now run past its own occurrence (N-Day Due Date), so it's no longer
    a stable identity for "which occurrence is this"."""
    rows = await db.execute(
        select(
            Task.source_template_id, Task.recurring_group_id,
            func.coalesce(Task.occurrence_date, Task.due_date),
        ).where(Task.plan_id == plan_id)
    )
    return {(sid, gid, d) for sid, gid, d in rows.all()}


def _period_bounds(period) -> dict:
    """`apply_due_days` kwargs that keep a recurring due date inside the
    period its occurrence landed in (empty -> unbounded, for a bare date)."""
    if period is None:
        return {}
    return {"period_start": period.start_date, "period_end": period.end_date}


def _subtask_due(s_tmpl, parent_due, period):
    """A pack sub-task template resolves its OWN day (+ anchor) against the
    period, capped so it's never after the parent task. A sub-task template
    is expected to always carry its own `due_day_of_period` (or a
    `frequency`) now — the close-pack editor no longer offers leaving it
    blank to inherit the parent's due date, and the create/update endpoints
    reject that combination (see close_packs._validate_sub_task_schedule).
    The blank/no-period fallback below only still fires for template rows
    saved before that change; it's a legacy safety net, not a supported
    configuration going forward.

    Returns (occurrence_date, due_date) — occurrence_date is always None
    here since this "day N of period" resolution has no separate occurrence
    concept (N-Day Due Date is a RECURRING-only feature; see
    _subtask_dates)."""
    if period is None or not isinstance(s_tmpl, dict) or not s_tmpl.get("due_day_of_period"):
        return None, parent_due
    d = resolve_generated_subtask_due_date(
        period.start_date, period.end_date,
        s_tmpl.get("due_day_of_period"),
        s_tmpl.get("due_anchor") or "PERIOD_START",
        _due_type(s_tmpl.get("due_date_type")),
        _post_period_days(s_tmpl),
        parent_due,
    )
    return None, d


def _subtask_dates(
    s_tmpl, parent_due, period, calendar=CalendarType.AD, parent_is_subperiod=False,
) -> list[tuple[Optional[date], date]]:
    """Every (occurrence_date, due_date) pair this sub-task template should
    have UNDER ONE parent task occurrence. Normally a single pair (see
    _subtask_due — occurrence_date is None there, it's not a recurring
    resolution). When the template has its own `frequency` — and the
    parent task occurs once per period, never for a DAILY/WEEKLY parent,
    which would make this window grow across every one of the parent's own
    occurrences — one pair per occurrence, spanning the sub-task's own
    recurrence_start (default: the period start) through the parent task's
    own due date (and recurrence_end, if earlier). due_date = occurrence
    + this sub-task's own `due_days` (N-Day Due Date), always capped so it
    never exceeds the parent task's own due date.

    A period-level parent gets a SEPARATE Task row per period (Bhadra's
    "Monthly Parent" and Ashwin's are two different Task ids), and this
    function is called once per occurrence to materialize THAT occurrence's
    own sub-tasks. `recurrence_start` is a fixed, absolute date set once on
    the template (e.g. Bhadra 1) — it must stay fixed as the recurrence's
    phase anchor (so "every 2nd week" keeps landing on the right weeks
    across period boundaries), but the WINDOW of dates actually returned
    here must never reach earlier than the period this occurrence belongs
    to. Using the raw anchor as both meant materializing Ashwin's occurrence
    re-walked every weekly date since Bhadra and attached them — again — to
    Ashwin's task, on top of the identical rows Bhadra's own task already
    got; Kartik's occurrence would then repeat both. Clamping the window's
    start to `period.start_date` (while still phasing off the true anchor)
    keeps each period's occurrence to only the dates newly due in it."""
    if not isinstance(s_tmpl, dict):
        return [_subtask_due(s_tmpl, parent_due, period)]
    freq = _freq(s_tmpl.get("frequency"))
    if not freq or not parent_due or parent_is_subperiod:
        return [_subtask_due(s_tmpl, parent_due, period)]
    due_days = _due_days(s_tmpl)
    # The OCCURRENCE search must be bounded by the latest occurrence whose
    # (occurrence + due_days) still lands on/before parent_due — not by
    # parent_due itself, since occurrence_dates() only knows how to bound
    # the raw occurrence date, never the offset due date.
    range_end = cap_occurrence_search_end(parent_due, due_days)
    r_end = _as_date(s_tmpl.get("recurrence_end"))
    if r_end and r_end < range_end:
        range_end = r_end
    anchor = (
        _as_date(s_tmpl.get("recurrence_start"))
        or (period.start_date if period else None)
        or range_end
    )
    window_start = max(anchor, period.start_date) if period else anchor
    if window_start > range_end:
        window_start = range_end
    # No `or [range_end]` fallback here on purpose: an empty `dates` means
    # there is genuinely no valid occurrence for this parent occurrence
    # (most commonly the sub-task's own recurrence_end has already passed —
    # `hard_end` inside occurrence_dates clips to it) — that must produce
    # NO sub-task row under this occurrence, not a synthetic one dated on
    # `range_end` (which used to keep re-materializing a "due" sub-task
    # past its own end date forever, one per new parent occurrence).
    occ_dates = occurrence_dates(
        frequency=freq, interval_count=s_tmpl.get("interval_count") or 1,
        days_of_week=s_tmpl.get("days_of_week"), start=anchor, end=r_end,
        range_start=window_start, range_end=range_end, calendar=calendar,
    )
    # Rolling a weekend/holiday due date forward can't carry it past
    # parent_due when parent_due is a working day (the bound above already
    # guarantees occurrence + due_days <= parent_due); min() keeps "a
    # sub-task is never due after its parent" true even for a parent left on a
    # non-working day.
    return [
        (d, min(apply_due_days(d, due_days, **_period_bounds(period)), parent_due))
        for d in occ_dates
    ]


def _add_subtasks(
    db, tenant_id, task, due, entity_id, subs, upcoming, period=None,
    calendar=CalendarType.AD, parent_is_subperiod=False,
):
    status = TaskStatus.UPCOMING if upcoming else TaskStatus.NOT_STARTED
    for s_tmpl in subs:
        # A dict WITH an "id" is a pack sub-task template; a dict without one
        # is a persisted custom-recurrence rule sub-task; a bare object is a
        # freshly-submitted custom sub-task. Only the first is "not custom".
        from_template = isinstance(s_tmpl, dict) and bool(s_tmpl.get("id"))
        base_title = s_tmpl["title"] if isinstance(s_tmpl, dict) else s_tmpl.title
        pairs = _subtask_dates(s_tmpl, due, period, calendar, parent_is_subperiod)
        multi = len(pairs) > 1
        for occ, d in pairs:
            suffix_date = occ or d
            db.add(
                SubTask(
                    tenant_id=tenant_id,
                    task_id=task.id,
                    entity_id=entity_id,
                    source_template_id=s_tmpl.get("id") if isinstance(s_tmpl, dict) else None,
                    is_custom=not from_template,
                    title=base_title + (f" — {_date_suffix(suffix_date, calendar)}" if multi else ""),
                    description=(s_tmpl.get("description") if isinstance(s_tmpl, dict) else s_tmpl.description),
                    due_date=d,
                    occurrence_date=occ,
                    due_date_type=DueDateType.WITHIN_PERIOD,
                    post_period_due_days=0,
                    due_days=_due_days(s_tmpl) if isinstance(s_tmpl, dict) else 0,
                    due_time=_tmpl_time(s_tmpl.get("due_time")) if isinstance(s_tmpl, dict) else None,
                    priority=_sub_priority(s_tmpl),
                    tags=_sub_tags(s_tmpl),
                    status=status,
                )
            )


def _preview_subtasks(s_tmpls, due, period, calendar, parent_is_subperiod) -> list[dict]:
    """Same title/date computation as `_add_subtasks`, minus the `db.add` —
    a plain list of dicts describing the sub-task rows a real generation
    would create under one Task occurrence due `due`."""
    out: list[dict] = []
    for s_tmpl in s_tmpls:
        base_title = s_tmpl["title"] if isinstance(s_tmpl, dict) else s_tmpl.title
        pairs = _subtask_dates(s_tmpl, due, period, calendar, parent_is_subperiod)
        multi = len(pairs) > 1
        for occ, d in pairs:
            suffix_date = occ or d
            out.append({
                "template_id": s_tmpl.get("id") if isinstance(s_tmpl, dict) else None,
                "title": base_title + (f" — {_date_suffix(suffix_date, calendar)}" if multi else ""),
                "description": s_tmpl.get("description") if isinstance(s_tmpl, dict) else s_tmpl.description,
                "frequency": s_tmpl.get("frequency") if isinstance(s_tmpl, dict) else None,
                "interval_count": (s_tmpl.get("interval_count") or 1) if isinstance(s_tmpl, dict) else 1,
                "due_date": d,
                "occurrence_date": occ,
                "due_date_type": "WITHIN_PERIOD",
                "post_period_due_days": 0,
                "due_days": _due_days(s_tmpl),
            })
    return out


def preview_tasks_for_period(
    task_templates: list[dict], period: Period, calendar: CalendarType = CalendarType.AD,
) -> list[dict]:
    """Read-only preview of what `generate_plans` WOULD materialize for one
    (pack, entity) pair in `period` — no DB access, no writes. Reuses the
    exact same pure date-math (`due_within`, `occurrence_dates`,
    `_subtask_dates`) as real generation, so a previewed date can never
    drift from what actually gets created once this period is generated
    for real. `task_templates` is the same dict shape a ClosePackVersion
    snapshot carries (see close_pack_service.build_snapshot_payload) — the
    caller passes the pack's LIVE template tree in that shape so the
    preview always reflects the latest edits, not a stale snapshot.

    Mirrors `_materialize_recurring` / `_materialize_once_per_period`'s
    per-period behavior:
      * non-recurring          -> one row, due on due_day_of_period.
      * DAILY / WEEKLY         -> one row per occurrence landing in the
        period (none at all if the period happens to catch none — same as
        real generation, which adds no placeholder for a sub-period task).
      * MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL -> one row per occurrence
        landing in the period, or — if none land in it — a single
        `occurs_in_period=False` row naming the next occurrence's date.
    """
    rows: list[dict] = []
    for t_tmpl in task_templates:
        freq = _freq(t_tmpl.get("frequency"))
        base_title = t_tmpl["title"]
        subs = t_tmpl.get("sub_task_templates") or []
        common = {
            "template_id": t_tmpl.get("id"),
            "description": t_tmpl.get("description"),
            "frequency": t_tmpl.get("frequency"),
            "interval_count": t_tmpl.get("interval_count") or 1,
            "priority": t_tmpl.get("priority") or "MEDIUM",
            "tags": list(t_tmpl.get("tags") or []),
            "due_date_type": "WITHIN_PERIOD",
            "post_period_due_days": 0,
        }
        due_days = _due_days(t_tmpl)

        if not freq:
            due = resolve_generated_due_date(
                period.start_date, period.end_date,
                t_tmpl.get("due_day_of_period"), t_tmpl.get("due_anchor") or "PERIOD_START",
                _due_type(t_tmpl.get("due_date_type")), _post_period_days(t_tmpl),
            )
            rows.append({
                **common, "title": base_title, "occurs_in_period": True,
                "due_date": due, "next_due_date": None, "occurrence_date": None, "due_days": 0,
                "subtasks": _preview_subtasks(subs, due, period, calendar, False),
            })
            continue

        sub_flag = freq in SUBPERIOD
        anchor = _as_date(t_tmpl.get("recurrence_start")) or period.start_date
        r_end = _as_date(t_tmpl.get("recurrence_end"))

        if sub_flag:
            dates = occurrence_dates(
                frequency=freq, interval_count=t_tmpl.get("interval_count") or 1,
                days_of_week=t_tmpl.get("days_of_week"), start=anchor, end=r_end,
                range_start=period.start_date, range_end=period.end_date, calendar=calendar,
            )
            multi = len(dates) > 1
            for d in dates:
                due = apply_due_days(d, due_days, **_period_bounds(period))
                rows.append({
                    **common,
                    "title": base_title + (f" — {_date_suffix(d, calendar)}" if multi else ""),
                    "occurs_in_period": True, "due_date": due, "next_due_date": None,
                    "occurrence_date": d, "due_days": due_days,
                    "subtasks": _preview_subtasks(subs, due, period, calendar, True),
                })
            continue

        # Period-level recurring: occurrences over a generous tail so a
        # period with no landing occurrence can still name the next one —
        # same window `_materialize_recurring` searches. `in_period` is
        # decided by the raw OCCURRENCE date, never the (possibly offset)
        # due date — an occurrence belongs to whichever period it actually
        # landed in, independent of when N-Day Due Date makes it due.
        all_occ = occurrence_dates(
            frequency=freq, interval_count=t_tmpl.get("interval_count") or 1,
            days_of_week=None, start=anchor, end=r_end,
            range_start=anchor, range_end=period.end_date + timedelta(days=2000),
            calendar=calendar,
        )
        in_period = [d for d in all_occ if period.start_date <= d <= period.end_date]
        if in_period:
            for d in in_period:
                due = apply_due_days(d, due_days, **_period_bounds(period))
                rows.append({
                    **common, "title": base_title, "occurs_in_period": True,
                    "due_date": due, "next_due_date": None,
                    "occurrence_date": d, "due_days": due_days,
                    "subtasks": _preview_subtasks(subs, due, period, calendar, False),
                })
        else:
            nxt = next((d for d in all_occ if d >= period.start_date), None)
            # No next occurrence at all (recurrence_end has passed) — the
            # task is over; don't show it as an "Upcoming" row in this or
            # any later period's preview. Mirrors _materialize_recurring's
            # own `if nxt is not None:` guard for real generation.
            if nxt is not None:
                rows.append({
                    **common, "title": base_title, "occurs_in_period": False,
                    "due_date": None, "next_due_date": apply_due_days(nxt, due_days),
                    "occurrence_date": None, "due_days": due_days, "subtasks": [],
                })
    return rows


def _period_for_date(window: list[Period], d: date) -> Optional[Period]:
    for p in window:
        if p.start_date <= d <= p.end_date:
            return p
    return None


async def _materialize_recurring(
    db, *, tenant_id, window, current_end, entity_id, owner_id, defn: dict,
    pack=None, version_number=None, recurring_group_id=None, subs=None,
    assigned_to_id=None, member_assigned_to_id=None, member_ids=None, priority=None, tags=None,
    calendar=CalendarType.AD, recurrence_rule=None, plan_cache: dict,
):
    """Materialise a recurring task across `window`.

      * DAILY / WEEKLY (sub-period): one Task per occurrence date.
      * MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL (period-level): EXACTLY
        ONE Task per period — a real, actionable task (due on the occurrence
        date) in a period the cycle lands on, and a locked "scheduled"
        placeholder (status UPCOMING, due = the NEXT occurrence date, no
        sub-tasks) in every other period so the task stays visible with its
        real next-due date.

    `defn` carries frequency / interval_count / days_of_week /
    recurrence_start / recurrence_end / title / description / id.

    `member_ids` (2+ Team Member ids) whole-entity-assigns every real
    occurrence to that hand-picked group instead of a single assignee —
    mutually exclusive with `member_assigned_to_id`. Unlike the single-
    assignee carry-forward below, this is only ever passed explicitly by
    the caller (custom "Add Task" creation, or the scheduler re-reading it
    back out of a representative task's `recurrence_rule`); there's no
    template-driven default for it.
    """
    freq = _freq(defn.get("frequency"))
    if not freq:
        return
    anchor = _as_date(defn.get("recurrence_start")) or window[0].start_date
    r_end = _as_date(defn.get("recurrence_end"))
    sub_flag = freq in SUBPERIOD
    # N-Day Due Date — every recurring cadence gets it (Daily through
    # Annual, and Custom); due_date = this occurrence's own landing date +
    # due_days, computed fresh per occurrence below.
    due_days = _due_days(defn)

    # A recurring TEMPLATE occurrence normally materializes unassigned (the
    # Lead assigns it once they own the plan). If the Lead has ALREADY
    # assigned an earlier occurrence of this same series/entity, carry that
    # assignee forward so they don't re-assign it every period. Only for
    # template series (defn id set) and only when the caller didn't pass an
    # explicit assignee (custom "Add Task" path already bakes one in).
    effective_member_id = member_assigned_to_id
    if effective_member_id is None and defn.get("id"):
        prev_member = await db.execute(
            select(Task.member_assigned_to_id)
            .where(
                Task.tenant_id == tenant_id,
                Task.entity_id == entity_id,
                Task.source_template_id == defn["id"],
                Task.member_assigned_to_id.is_not(None),
            )
            .order_by(Task.due_date.desc())
            .limit(1)
        )
        effective_member_id = prev_member.scalar_one_or_none()

    async def _plan_for(period):
        if period.id not in plan_cache:
            plan_cache[period.id] = await _get_or_create_plan(
                db, tenant_id=tenant_id, period=period, entity_id=entity_id,
                owner_id=owner_id, pack=pack, version_number=version_number,
            )
        return plan_cache[period.id]

    async def _add(period, plan, d, *, placeholder):
        # `d` is always the raw OCCURRENCE date (the date this instance
        # actually landed on, per its frequency) — the idempotency key, the
        # title-suffix identity, and (for a placeholder) the "next occurs"
        # pointer are all about the occurrence, never the offset due date.
        keys = await _existing_keys(db, plan.id)
        if (defn.get("id"), recurring_group_id, d) in keys:
            return 0
        # A real occurrence is due inside the period it landed in; a
        # placeholder's `d` is the NEXT occurrence, in a later period, and
        # keeps naming that date.
        due = apply_due_days(d, due_days, **({} if placeholder else _period_bounds(period)))
        real_upcoming = period.start_date > current_end
        multi_ids = None if placeholder else (set(member_ids) if member_ids else None)
        member_id = None if (placeholder or multi_ids) else effective_member_id
        if placeholder or real_upcoming:
            status = TaskStatus.UPCOMING
        else:
            status = TaskStatus.ASSIGNED if (member_id or multi_ids) else TaskStatus.NOT_STARTED
        task = Task(
            tenant_id=tenant_id,
            plan_id=plan.id,
            entity_id=entity_id,
            source_template_id=defn.get("id"),
            recurring_group_id=recurring_group_id,
            is_custom=recurring_group_id is not None,
            is_required=defn.get("is_required", True),
            assigned_to_id=None if placeholder else assigned_to_id,
            member_assigned_to_id=member_id,
            assigned_entity_wide=bool(multi_ids),
            title=defn["title"] + (f" — {_date_suffix(d, calendar)}" if sub_flag else ""),
            description=defn.get("description"),
            due_date=due,
            occurrence_date=d,
            due_days=due_days,
            due_time=_tmpl_time(defn.get("due_time")),
            priority=priority or Priority.MEDIUM,
            tags=list(tags or []),
            status=status,
            recurrence_rule=recurrence_rule,
        )
        db.add(task)
        await db.flush()
        if multi_ids:
            for uid in multi_ids:
                db.add(TaskEntityAssignee(tenant_id=tenant_id, task_id=task.id, user_id=uid))
        if not placeholder:
            # Sub-tasks are capped against the parent's actual DUE date
            # (`due`), not its raw occurrence (`d`) — "subtask <= parent
            # due date" means the offset deadline, same as everywhere else
            # in this feature.
            _add_subtasks(db, tenant_id, task, due, entity_id, subs or [],
                          status == TaskStatus.UPCOMING, period,
                          calendar=calendar, parent_is_subperiod=sub_flag)
        await db.flush()
        return 1

    # --- sub-period: one task per occurrence, no placeholders --------------
    if sub_flag:
        dates = occurrence_dates(
            frequency=freq, interval_count=defn.get("interval_count") or 1,
            days_of_week=defn.get("days_of_week"), start=anchor, end=r_end,
            range_start=window[0].start_date, range_end=window[-1].end_date,
            calendar=calendar,
        )
        for d in dates:
            period = _period_for_date(window, d)
            if period:
                await _add(period, await _plan_for(period), d, placeholder=False)
        return

    # --- period-level: one task per period (real or placeholder) ----------
    # occurrences over the window + a generous tail so a placeholder in the
    # last window period can still name its next-due date.
    all_occ = occurrence_dates(
        frequency=freq, interval_count=defn.get("interval_count") or 1,
        days_of_week=None, start=anchor, end=r_end,
        range_start=anchor,
        range_end=window[-1].end_date + timedelta(days=2000),
        calendar=calendar,
    )
    for period in window:
        in_period = [d for d in all_occ if period.start_date <= d <= period.end_date]
        plan = await _plan_for(period)
        if in_period:
            for d in in_period:
                await _add(period, plan, d, placeholder=False)
        else:
            nxt = next((d for d in all_occ if d >= period.start_date), None)
            if nxt is not None:
                await _add(period, plan, nxt, placeholder=True)
            else:
                # No occurrence left at all (recurrence_end has passed) —
                # remove any placeholder a PRIOR run already materialized
                # for this period/series instead of leaving it stranded
                # forever as "Upcoming" with no real next-due date. Self-
                # healing: applies to any task/sub-task series, not just
                # this one, every time generation touches this period.
                await _prune_stale_placeholder(
                    db, tenant_id, plan.id, period, defn.get("id"), recurring_group_id,
                )


async def _prune_stale_placeholder(
    db, tenant_id, plan_id, period, template_id, recurring_group_id,
) -> None:
    """Delete a previously-materialized "scheduled" placeholder (status
    UPCOMING, OCCURRENCE pointing OUTSIDE this period — exactly how the
    frontend already tells a placeholder apart from a real, in-period
    occurrence, see toPlanTaskRow's `scheduled` flag) for this task series
    in this period. The reverse of `_add(..., placeholder=True)` above —
    never touches a real occurrence (those LAND inside their own period by
    construction) or anything a Lead/Member has actually started.

    Bounded by `occurrence_date` (coalesced to `due_date` for a non-
    recurring row, which has no separate occurrence), never by `due_date`
    directly — N-Day Due Date can push a real occurrence's due_date past
    its own period's end, which must never be mistaken for a stale
    placeholder pointing at a different period."""
    if not template_id and not recurring_group_id:
        return
    occ = func.coalesce(Task.occurrence_date, Task.due_date)
    conditions = [
        Task.tenant_id == tenant_id,
        Task.plan_id == plan_id,
        Task.status == TaskStatus.UPCOMING,
        or_(occ < period.start_date, occ > period.end_date),
    ]
    conditions.append(
        Task.source_template_id == template_id if template_id
        else Task.recurring_group_id == recurring_group_id
    )
    rows = await db.execute(select(Task.id).where(*conditions))
    stale_ids = [tid for (tid,) in rows.all()]
    if not stale_ids:
        return
    await db.execute(delete(SubTask).where(SubTask.task_id.in_(stale_ids)))
    await db.execute(delete(Task).where(Task.id.in_(stale_ids)))
    await db.flush()


async def _materialize_once_per_period(
    db, *, tenant_id, window, current_end, entity_id, owner_id, t_tmpl,
    pack, version_number, plan_cache: dict,
):
    for period in window:
        if period.id not in plan_cache:
            plan_cache[period.id] = await _get_or_create_plan(
                db, tenant_id=tenant_id, period=period, entity_id=entity_id,
                owner_id=owner_id, pack=pack, version_number=version_number,
            )
        plan = plan_cache[period.id]
        keys = await _existing_keys(db, plan.id)
        due_args = (
            period.start_date, period.end_date,
            t_tmpl.get("due_day_of_period"),
            t_tmpl.get("due_anchor") or "PERIOD_START",
            _due_type(t_tmpl.get("due_date_type")), _post_period_days(t_tmpl),
        )
        due = resolve_generated_due_date(*due_args)
        # A task generated before non-working due dates were rolled to a
        # working day sits on the raw date (a Saturday, say) — it must still
        # count as "already generated", or the next top-up would add a second
        # copy on the rolled Monday. (`_apply_template_fields` then moves the
        # old one to the rolled date if it hasn't been started.)
        raw_due = resolve_generated_due_date(*due_args, roll_non_working=False)
        if (t_tmpl.get("id"), None, due) in keys or (t_tmpl.get("id"), None, raw_due) in keys:
            continue
        upcoming = period.start_date > current_end
        task = Task(
            tenant_id=tenant_id,
            plan_id=plan.id,
            entity_id=entity_id,
            source_template_id=t_tmpl.get("id"),
            is_custom=False,
            title=t_tmpl["title"],
            description=t_tmpl.get("description"),
            due_date=due,
            due_date_type=DueDateType.WITHIN_PERIOD,
            post_period_due_days=0,
            due_time=_tmpl_time(t_tmpl.get("due_time")),
            priority=_tmpl_priority(t_tmpl.get("priority")),
            tags=list(t_tmpl.get("tags") or []),
            status=TaskStatus.UPCOMING if upcoming else TaskStatus.NOT_STARTED,
        )
        db.add(task)
        await db.flush()
        _add_subtasks(
            db, tenant_id, task, due, entity_id, t_tmpl.get("sub_task_templates", []),
            upcoming, period, calendar=pack.calendar_type if pack else CalendarType.AD,
        )
        await db.flush()


async def materialize_plan_for_entity(
    db, *, tenant_id, pack, version_number, task_templates, period, entity_id, owner_id, upcoming=False
) -> Plan:
    """Single-period top-up (period close). Delegates to the same paths as
    generate_plans but bounded to one period's window."""
    window = [period]
    current_end = period.end_date if not upcoming else period.start_date - timedelta(days=1)
    plan_cache: dict = {}
    cal = pack.calendar_type if pack else CalendarType.AD
    for t_tmpl in task_templates:
        if _freq(t_tmpl.get("frequency")):
            await _materialize_recurring(
                db, tenant_id=tenant_id, window=window, current_end=current_end,
                entity_id=entity_id, owner_id=owner_id, defn=t_tmpl,
                pack=pack, version_number=version_number, calendar=cal,
                subs=t_tmpl.get("sub_task_templates", []), plan_cache=plan_cache,
            )
        else:
            await _materialize_once_per_period(
                db, tenant_id=tenant_id, window=window, current_end=current_end,
                entity_id=entity_id, owner_id=owner_id, t_tmpl=t_tmpl,
                pack=pack, version_number=version_number, plan_cache=plan_cache,
            )
    return plan_cache.get(period.id) or await _get_or_create_plan(
        db, tenant_id=tenant_id, period=period, entity_id=entity_id,
        owner_id=owner_id, pack=pack, version_number=version_number,
    )


async def materialize_custom_recurring_task(
    db: AsyncSession, *, tenant_id: str, start_plan: Plan, payload, horizon: int = DEFAULT_HORIZON,
    member_ids: Optional[set[str]] = None,
) -> Optional[Task]:
    start_period = (
        await db.execute(select(Period).where(Period.id == start_plan.period_id))
    ).scalar_one()
    window = await _ordered_window(db, tenant_id, start_period, horizon)
    current_end = await _current_period_end(db, tenant_id, start_period)
    group_id = str(uuid.uuid4())
    has_subs = bool(payload.subtasks)
    # Mutually exclusive with sub-tasks, same rule as the single-assignee
    # member_assigned_to_id below — with sub-tasks present, assignment lives
    # on each sub-task instead of the task itself.
    member_ids = None if has_subs else member_ids
    anchor = payload.due_date or start_period.start_date
    defn = {
        "id": None,
        "title": payload.title,
        "description": payload.description,
        "frequency": payload.frequency,
        "interval_count": payload.interval_count,
        "days_of_week": payload.days_of_week,
        "is_required": payload.is_required,
        "recurrence_start": anchor,
        "recurrence_end": payload.recurrence_end,
        "due_time": payload.due_time,
        "due_days": payload.due_days,
    }
    cal = CalendarType.AD
    if start_plan.source_pack_id:
        pk = (
            await db.execute(select(ClosePack).where(ClosePack.id == start_plan.source_pack_id))
        ).scalar_one_or_none()
        if pk:
            cal = pk.calendar_type
    # Persisted on every instance so the period-close scheduler can extend
    # this recurring task forward one period at a time (DEFAULT_HORIZON is
    # small — no more front-loading years of instances).
    rule = {
        "title": payload.title,
        "description": payload.description,
        "frequency": payload.frequency.value if payload.frequency else None,
        "interval_count": payload.interval_count or 1,
        "days_of_week": list(payload.days_of_week) if payload.days_of_week else None,
        "is_required": payload.is_required,
        "anchor": anchor.isoformat() if hasattr(anchor, "isoformat") else str(anchor),
        "end": payload.recurrence_end.isoformat() if payload.recurrence_end else None,
        "calendar": cal.value,
        "source_pack_id": start_plan.source_pack_id,
        "assigned_to_id": payload.assigned_to_id,
        "member_assigned_to_id": None if has_subs else payload.member_assigned_to_id,
        # 2+ hand-picked assignees (whole-group, not a single owner) — kept
        # here too so extend_recurring_custom_tasks() re-applies the same
        # group to every future period it materializes, not just the batch
        # created right now.
        "member_ids": sorted(member_ids) if member_ids else None,
        "priority": (payload.priority.value if payload.priority else None),
        "tags": list(payload.tags or []),
        "due_days": payload.due_days,
        "due_time": payload.due_time.isoformat() if payload.due_time else None,
        "subtasks": [
            {
                "title": s.title,
                "description": getattr(s, "description", None),
                "priority": (s.priority.value if getattr(s, "priority", None) else None),
                "tags": list(getattr(s, "tags", None) or []),
            }
            for s in (payload.subtasks or [])
        ],
    }
    plan_cache = {start_period.id: start_plan}
    await _materialize_recurring(
        db, tenant_id=tenant_id, window=window, current_end=current_end,
        entity_id=start_plan.entity_id, owner_id=start_plan.owner_id, defn=defn,
        recurring_group_id=group_id, subs=payload.subtasks,
        assigned_to_id=payload.assigned_to_id,
        member_assigned_to_id=None if has_subs else payload.member_assigned_to_id,
        member_ids=member_ids,
        priority=payload.priority, tags=payload.tags, calendar=cal,
        recurrence_rule=rule, plan_cache=plan_cache,
    )
    first = (
        await db.execute(
            select(Task)
            .where(Task.recurring_group_id == group_id, Task.plan_id == start_plan.id)
            .order_by(Task.due_date)
            .limit(1)
        )
    ).scalar_one_or_none()
    return first


def _freq_from_str(raw) -> Optional[RecurrenceFrequency]:
    if not raw:
        return None
    try:
        return RecurrenceFrequency(raw)
    except ValueError:
        return None


async def _recurring_group_reps(db: AsyncSession, tenant_id: str) -> dict[str, Task]:
    """One representative (the earliest) instance per custom recurring task
    that carries a rule, keyed by `recurring_group_id`. Its stored
    `recurrence_rule` is what the series is read back from — by the
    period-close extension below and by the upcoming-period preview."""
    rows = await db.execute(
        select(Task)
        .join(Plan, Task.plan_id == Plan.id)
        .where(
            Task.tenant_id == tenant_id,
            Task.recurring_group_id.is_not(None),
            Task.recurrence_rule.is_not(None),
        )
        .order_by(Task.recurring_group_id, Task.due_date, Task.id)
        .distinct(Task.recurring_group_id)
    )
    return {t.recurring_group_id: t for t in rows.scalars().all()}


def _custom_rule_defn(rep: Task, rule: dict, freq: RecurrenceFrequency) -> dict:
    """The recurrence definition `_materialize_recurring` (and the preview)
    work from, read back out of a custom recurring task's stored rule."""
    end_raw = rule.get("end")
    return {
        "id": None,
        "title": rule.get("title") or rep.title,
        "description": rule.get("description"),
        "frequency": freq,
        "interval_count": rule.get("interval_count") or 1,
        "days_of_week": rule.get("days_of_week"),
        "is_required": rule.get("is_required", True),
        # The anchor must stay in OCCURRENCE-date space (never the
        # offset due_date) so the recurrence's weekly/monthly/etc phase
        # doesn't drift once N-Day Due Date is in play.
        "recurrence_start": _as_date(rule.get("anchor")) or rep.occurrence_date or rep.due_date,
        "recurrence_end": _as_date(end_raw) if end_raw else None,
        "due_time": rule.get("due_time"),
        "due_days": rule.get("due_days") or 0,
    }


def _rule_calendar(rule: dict) -> CalendarType:
    try:
        return CalendarType(rule.get("calendar") or "AD")
    except ValueError:
        return CalendarType.AD


def preview_custom_rule_for_period(rep: Task, rule: dict, period: Period) -> list[dict]:
    """Read-only preview of the rows one custom recurring task (a Lead's "Add
    Task" with a repeat) would get in `period` — the same date maths real
    generation uses (`extend_recurring_custom_tasks` -> `_materialize_recurring`),
    fed the same definition. No DB access, no writes."""
    freq = _freq_from_str(rule.get("frequency"))
    if not freq:
        return []
    tmpl = {
        **_custom_rule_defn(rep, rule, freq),
        "sub_task_templates": rule.get("subtasks") or [],
        "priority": rule.get("priority"),
        "tags": rule.get("tags") or [],
    }
    rows = preview_tasks_for_period([tmpl], period, _rule_calendar(rule))
    for row in rows:
        row["is_custom"] = True
    return rows


@dataclass
class CustomPreviewGroup:
    """The previewed rows of every custom recurring task that belongs to one
    (Close Pack, entity) pairing — `pack_id` is None for tasks added to a plan
    with no Close Pack behind it."""

    pack_id: Optional[str]
    entity_id: str
    owner_id: Optional[str]
    tasks: list[dict] = field(default_factory=list)


def _custom_preview_groups(
    reps: dict[str, Task],
    rep_plans: dict[str, Plan],
    plan_periods: dict[str, Period],
    period: Period,
) -> list[CustomPreviewGroup]:
    groups: dict[tuple, CustomPreviewGroup] = {}
    for rep in reps.values():
        plan = rep_plans.get(rep.plan_id)
        if plan is None:
            continue
        # A task that was added in some period doesn't exist before it: an
        # earlier period gets no "upcoming" row for a series that hadn't begun.
        origin = plan_periods.get(plan.period_id)
        if origin is not None and period.start_date < origin.start_date:
            continue
        rows = preview_custom_rule_for_period(rep, rep.recurrence_rule or {}, period)
        if not rows:
            continue
        group = groups.setdefault(
            (plan.source_pack_id, rep.entity_id),
            CustomPreviewGroup(plan.source_pack_id, rep.entity_id, plan.owner_id),
        )
        group.owner_id = group.owner_id or plan.owner_id
        group.tasks.extend(rows)
    for group in groups.values():
        # Real rows first (soonest due), then the "Upcoming" placeholders.
        group.tasks.sort(key=lambda t: (
            not t["occurs_in_period"],
            t["due_date"] or t["next_due_date"] or date.max,
            t["title"],
        ))
    return list(groups.values())


async def preview_custom_recurring_for_period(
    db: AsyncSession, *, tenant_id: str, period: Period,
) -> list[CustomPreviewGroup]:
    """What the tenant's custom recurring tasks WOULD add to `period` once it
    is generated (period close materialises them one period at a time), grouped
    by the (pack, entity) plan they belong to. Read-only."""
    reps = await _recurring_group_reps(db, tenant_id)
    if not reps:
        return []
    rep_plans = {
        p.id: p
        for p in (
            await db.execute(select(Plan).where(Plan.id.in_({t.plan_id for t in reps.values()})))
        ).scalars().all()
    }
    plan_periods = {
        p.id: p
        for p in (
            await db.execute(
                select(Period).where(Period.id.in_({p.period_id for p in rep_plans.values()}))
            )
        ).scalars().all()
    }
    return _custom_preview_groups(reps, rep_plans, plan_periods, period)


async def extend_recurring_custom_tasks(
    db: AsyncSession,
    *,
    tenant_id: str,
    start_period: Period,
    horizon: int = DEFAULT_HORIZON,
) -> int:
    """Materialise the NEXT occurrences of every ACTIVE custom recurring
    task (Lead "Add Task" with a frequency) into the rolling window that
    starts at `start_period`. Idempotent — instances that already exist are
    skipped. The pack-template equivalent is handled by generate_plans /
    _regenerate_plans_for_next_period; this is the piece that keeps custom
    recurring tasks alive now that the horizon is short.

    Returns the number of task instances created.
    """
    window = await _ordered_window(db, tenant_id, start_period, horizon)
    if not window:
        return 0
    current_end = await _current_period_end(db, tenant_id, start_period)
    win_start, win_end = window[0].start_date, window[-1].end_date

    # One representative (earliest) instance per recurring group that
    # carries a rule.
    reps = await _recurring_group_reps(db, tenant_id)
    if not reps:
        return 0

    # The representative's own plan — its source_pack_id + owner decide
    # which plan in each future period the extended instances belong to.
    plan_ids = {t.plan_id for t in reps.values()}
    rep_plans = {
        p.id: p
        for p in (
            await db.execute(select(Plan).where(Plan.id.in_(plan_ids)))
        ).scalars().all()
    }

    async def _plan_cache_for(rep: Task) -> dict:
        """period_id -> the Plan (matching the rep's pack) for each window
        period, creating it if missing."""
        src = rep_plans.get(rep.plan_id)
        spid = src.source_pack_id if src else None
        cache: dict = {}
        for w in window:
            q = select(Plan).where(
                Plan.tenant_id == tenant_id,
                Plan.period_id == w.id,
                Plan.entity_id == rep.entity_id,
                Plan.source_pack_id == spid
                if spid
                else Plan.source_pack_id.is_(None),
            ).limit(1)
            pl = (await db.execute(q)).scalar_one_or_none()
            if not pl:
                pl = Plan(
                    tenant_id=tenant_id,
                    period_id=w.id,
                    entity_id=rep.entity_id,
                    owner_id=src.owner_id if src else None,
                    source_pack_id=spid,
                    source_pack_version=src.source_pack_version if src else None,
                    title=(src.title.split(" — ")[0] + f" — {w.label}")
                    if (src and src.title)
                    else w.label,
                    status=PlanStatus.OPEN,
                    due_date=w.end_date,
                )
                db.add(pl)
                await db.flush()
            cache[w.id] = pl
        return cache

    created = 0
    for group_id, rep in reps.items():
        rule = rep.recurrence_rule or {}
        freq = _freq_from_str(rule.get("frequency"))
        if not freq:
            continue
        # No early skip when the recurrence has already ended: still call
        # _materialize_recurring below so it can prune a stale placeholder
        # a PRIOR period-close already left in this window, exactly like
        # the pack-template path — an early return here would silently
        # skip that cleanup for custom recurring tasks.

        before = await _count_tasks_for_group(db, tenant_id, group_id, win_start, win_end)
        defn = _custom_rule_defn(rep, rule, freq)
        cal = _rule_calendar(rule)
        await _materialize_recurring(
            db,
            tenant_id=tenant_id,
            window=window,
            current_end=current_end,
            entity_id=rep.entity_id,
            owner_id=(rep_plans.get(rep.plan_id).owner_id if rep_plans.get(rep.plan_id) else None),
            defn=defn,
            recurring_group_id=group_id,
            subs=rule.get("subtasks") or [],
            assigned_to_id=rule.get("assigned_to_id"),
            member_assigned_to_id=rule.get("member_assigned_to_id"),
            member_ids=set(rule["member_ids"]) if rule.get("member_ids") else None,
            priority=Priority(rule["priority"]) if rule.get("priority") else None,
            tags=rule.get("tags") or [],
            calendar=cal,
            recurrence_rule=rule,
            plan_cache=await _plan_cache_for(rep),
        )
        after = await _count_tasks_for_group(db, tenant_id, group_id, win_start, win_end)
        created += max(0, after - before)

    return created


async def _count_tasks_for_group(
    db, tenant_id: str, group_id: str, start: date, end: date
) -> int:
    """Counts by OCCURRENCE (coalesced to due_date), not raw due_date —
    N-Day Due Date can push due_date outside [start, end] for an occurrence
    that still genuinely belongs to this window."""
    occ = func.coalesce(Task.occurrence_date, Task.due_date)
    return (
        await db.execute(
            select(func.count(Task.id)).where(
                Task.tenant_id == tenant_id,
                Task.recurring_group_id == group_id,
                occ >= start,
                occ <= end,
            )
        )
    ).scalar() or 0


async def _primary_member_for_entity(db, tenant_id, entity_id) -> Optional[str]:
    row = await db.execute(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.role == Role.TEAM_MEMBER,
            User.primary_entity_id == entity_id,
        ).limit(1)
    )
    return row.scalar_one_or_none()


async def _delete_existing_generated_plans(db, tenant_id, period_id, entity_id) -> None:
    rows = await db.execute(
        select(Plan).where(
            Plan.tenant_id == tenant_id,
            Plan.period_id == period_id,
            Plan.entity_id == entity_id,
        )
    )
    for plan in rows.scalars().all():
        await db.delete(plan)
    await db.flush()


async def generate_plans(
    db: AsyncSession,
    tenant_id: str,
    pack_id: str,
    period_id: str,
    entity_ids: List[str],
    user_id: Optional[str],
    owner_id: Optional[str] = None,
    horizon: int = DEFAULT_HORIZON,
) -> List[Plan]:
    pack = (
        await db.execute(
            select(ClosePack).where(ClosePack.id == pack_id, ClosePack.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not pack:
        raise ValueError("Close pack not found")

    start_period = (
        await db.execute(
            select(Period).where(Period.id == period_id, Period.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not start_period:
        raise ValueError("Period not found")

    if not entity_ids:
        raise ValueError("No entities provided")

    ent_rows = await db.execute(
        select(Entity).where(Entity.id.in_(entity_ids), Entity.tenant_id == tenant_id)
    )
    entities = list(ent_rows.scalars().all())
    if len(entities) != len(set(entity_ids)):
        raise ValueError("One or more entities not in tenant scope")

    if owner_id:
        owner_user = (
            await db.execute(select(User).where(User.id == owner_id, User.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if not owner_user:
            raise ValueError("Plan owner not found in tenant")
        if owner_user.role not in (Role.TEAM_LEAD, Role.SUPER_ADMIN):
            raise ValueError("Plan owner must be a Team Lead or Super Admin")

    version = await snapshot_pack(db, tenant_id, pack, user_id)
    task_templates = version.snapshot_json.get("task_templates", [])

    window = await _ordered_window(db, tenant_id, start_period, horizon)
    if not window:
        return []
    current_end = await _current_period_end(db, tenant_id, start_period)

    touched: list[Plan] = []
    for ent in entities:
        plan_cache: dict = {}
        for t_tmpl in task_templates:
            if _freq(t_tmpl.get("frequency")):
                await _materialize_recurring(
                    db, tenant_id=tenant_id, window=window, current_end=current_end,
                    entity_id=ent.id, owner_id=owner_id, defn=t_tmpl,
                    pack=pack, version_number=version.version_number,
                    calendar=pack.calendar_type,
                    subs=t_tmpl.get("sub_task_templates", []), plan_cache=plan_cache,
                    priority=_tmpl_priority(t_tmpl.get("priority")),
                    tags=list(t_tmpl.get("tags") or []),
                )
            else:
                await _materialize_once_per_period(
                    db, tenant_id=tenant_id, window=window, current_end=current_end,
                    entity_id=ent.id, owner_id=owner_id, t_tmpl=t_tmpl,
                    pack=pack, version_number=version.version_number, plan_cache=plan_cache,
                )
        for pl in plan_cache.values():
            if pl not in touched:
                touched.append(pl)

        await log_action(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            action="PLAN_GENERATED",
            entity_type="ClosePack",
            entity_id=pack.id,
            meta={
                "pack_version": version.version_number,
                "entity_id": ent.id,
                "start_period_id": start_period.id,
                "periods": len(window),
            },
        )

    return touched


# ============================================================
# Live sync — propagate a Close Pack edit into already-generated plans
# ============================================================
# A generated Task/SubTask is safe to auto-change only while nobody has
# actually done any work on it. ASSIGNED means someone's been designated
# as the owner — nothing has actually started yet, so a rename/re-date on
# the template must still reach it (confirmed against real data: a task
# generated as "TDS" stayed "TDS" forever after being assigned, even once
# its template was renamed to "TDS AP" — this tuple used to exclude
# ASSIGNED, so `_apply_template_fields`/`_reconcile_subtasks` silently gave
# up on it the moment a Lead picked an owner). IN_PROGRESS and anything
# further along (PENDING_REVIEW, COMPLETED, REJECTED, CARRIED_FORWARD) is
# still left untouched — that's where real work is actually happening.
_SYNCABLE = (TaskStatus.NOT_STARTED, TaskStatus.ASSIGNED, TaskStatus.UPCOMING)


async def _count_plan_tasks(db, plan_id: str) -> int:
    return (
        await db.execute(select(func.count(Task.id)).where(Task.plan_id == plan_id))
    ).scalar() or 0


def _apply_template_fields(
    task: Task, tmpl: dict, period: Period, calendar: CalendarType = CalendarType.AD
) -> bool:
    """Push a template's editable fields onto a not-yet-started Task.

    Title syncs for EVERY template, recurring or not — a sub-period
    (DAILY/WEEKLY) instance's title is the template's title plus its own
    occurrence-date suffix (`_materialize_recurring`'s own convention,
    e.g. "Trade Receivables — Aug 17"), so a rename must still reach it;
    only the suffix itself is preserved untouched, taken from the
    instance's own `occurrence_date` (never `due_date`, which may now sit
    N days later than the occurrence it names — see N-Day Due Date). A
    period-level recurring title (MONTHLY/QUARTERLY/SEMI_ANNUAL/ANNUAL)
    carries no date suffix at all, same as a non-recurring one.

    Due date IS rewritten for both non-recurring and recurring templates —
    a non-recurring due date always comes from due_day_of_period/anchor
    against `period`; a recurring instance's due date is `occurrence_date +
    due_days`, where `occurrence_date` is the FIXED calendar date this
    instance already earned by landing there (never recomputed — only the
    offset can change) and `due_days` comes fresh from the template, so an
    admin editing "Due Days" reaches every not-yet-started instance.
    """
    changed = False
    is_recurring = bool(tmpl.get("frequency"))
    is_subperiod = bool(is_recurring and _freq(tmpl.get("frequency")) in SUBPERIOD)
    suffix_date = task.occurrence_date if is_recurring else task.due_date
    new_title = tmpl["title"] + (
        f" — {_date_suffix(suffix_date, calendar)}" if is_subperiod and suffix_date else ""
    )
    if task.title != new_title:
        task.title = new_title
        changed = True
    if is_recurring:
        new_due_days = _due_days(tmpl)
        if task.occurrence_date is not None:
            in_period = period.start_date <= task.occurrence_date <= period.end_date
            new_due = apply_due_days(
                task.occurrence_date, new_due_days,
                **(_period_bounds(period) if in_period else {}),
            )
            if task.due_date != new_due:
                task.due_date = new_due
                changed = True
        if task.due_days != new_due_days:
            task.due_days = new_due_days
            changed = True
    else:
        new_due = resolve_generated_due_date(
            period.start_date, period.end_date,
            tmpl.get("due_day_of_period"),
            tmpl.get("due_anchor") or "PERIOD_START",
            _due_type(tmpl.get("due_date_type")), _post_period_days(tmpl),
        )
        new_type, new_days = DueDateType.WITHIN_PERIOD, 0
        if task.due_date != new_due:
            task.due_date = new_due
            changed = True
        if task.due_date_type != new_type:
            task.due_date_type = new_type
            changed = True
        if task.post_period_due_days != new_days:
            task.post_period_due_days = new_days
            changed = True
    desc = tmpl.get("description")
    if task.description != desc:
        task.description = desc
        changed = True
    tt = _tmpl_time(tmpl.get("due_time"))
    if task.due_time != tt:
        task.due_time = tt
        changed = True
    tp = _tmpl_priority(tmpl.get("priority"))
    if task.priority != tp:
        task.priority = tp
        changed = True
    ttags = list(tmpl.get("tags") or [])
    if task.tags != ttags:
        task.tags = ttags
        changed = True
    treq = bool(tmpl.get("is_required", True))
    if task.is_required != treq:
        task.is_required = treq
        changed = True
    return changed


async def _reconcile_subtasks(
    db, tenant_id: str, task: Task, sub_templates: list[dict], upcoming: bool,
    period: Period | None = None, calendar=CalendarType.AD,
    parent_is_subperiod: bool = False,
) -> tuple[int, int]:
    """Make `task`'s template-sourced sub-tasks match `sub_templates`: add
    any missing occurrence, drop any whose template — or, for a recurring
    sub-task template, whose specific occurrence date — is no longer
    wanted (only while still not-started). Custom (Lead-added) sub-tasks
    are never touched.

    A single-occurrence sub-task template (the common case) is matched to
    its existing SubTask by TEMPLATE, not by exact date — its due_date is
    then updated in place, same row, same id, even when the date itself
    changed. That matters: a delete-and-recreate on a date change (the old
    behavior, keyed by (template, due_date)) silently threw away that
    sub-task's assignment, attachments and comments, and handed the caller
    a different id than the one they were just looking at. A recurring
    sub-task template (2+ occurrences) still reconciles by exact date,
    since "which existing row corresponds to which new occurrence" isn't
    otherwise well-defined."""
    by_template: dict[str, list[SubTask]] = {}
    for s in task.sub_tasks:
        if s.source_template_id:
            by_template.setdefault(s.source_template_id, []).append(s)

    status = TaskStatus.UPCOMING if upcoming else TaskStatus.NOT_STARTED
    added = removed = 0
    seen_ids: set[str] = set()

    for st in sub_templates:
        sid = st["id"]
        pairs = _subtask_dates(st, task.due_date, period, calendar, parent_is_subperiod)
        multi = len(pairs) > 1
        existing_rows = by_template.get(sid, [])

        if len(pairs) == 1 and len(existing_rows) == 1:
            s = existing_rows[0]
            seen_ids.add(s.id)
            if s.status in _SYNCABLE:
                occ, d = pairs[0]
                if s.title != st["title"]:
                    s.title = st["title"]
                if s.description != st.get("description"):
                    s.description = st.get("description")
                if s.due_date != d:
                    s.due_date = d
                if s.occurrence_date != occ:
                    s.occurrence_date = occ
                if s.due_date_type != DueDateType.WITHIN_PERIOD:
                    s.due_date_type = DueDateType.WITHIN_PERIOD
                if s.post_period_due_days != 0:
                    s.post_period_due_days = 0
                new_ddays = _due_days(st)
                if s.due_days != new_ddays:
                    s.due_days = new_ddays
                new_due_time = _tmpl_time(st.get("due_time"))
                if s.due_time != new_due_time:
                    s.due_time = new_due_time
            continue

        # Multi-occurrence still reconciles by exact DUE date (see
        # docstring) — a due_days edit shifts every due date at once, so an
        # admin changing it on a multi-occurrence recurring sub-task
        # template will re-create those rows (losing assignment/
        # attachments) rather than shift them in place. Same documented
        # limitation as any other date change here.
        have_by_date = {s.due_date: s for s in existing_rows}
        for occ, d in pairs:
            suffix_date = occ or d
            new_title = st["title"] + (f" — {_date_suffix(suffix_date, calendar)}" if multi else "")
            s = have_by_date.get(d)
            if s is not None:
                seen_ids.add(s.id)
                if s.status in _SYNCABLE:
                    if s.title != new_title:
                        s.title = new_title
                    if s.description != st.get("description"):
                        s.description = st.get("description")
                continue
            db.add(
                SubTask(
                    tenant_id=tenant_id,
                    task_id=task.id,
                    entity_id=task.entity_id,
                    source_template_id=sid,
                    is_custom=False,
                    title=new_title,
                    description=st.get("description"),
                    due_date=d,
                    occurrence_date=occ,
                    due_date_type=DueDateType.WITHIN_PERIOD,
                    post_period_due_days=0,
                    due_days=_due_days(st),
                    due_time=_tmpl_time(st.get("due_time")),
                    status=status,
                )
            )
            added += 1

    for rows in by_template.values():
        for s in rows:
            if s.id not in seen_ids and s.status in _SYNCABLE:
                await db.delete(s)
                removed += 1

    return added, removed


async def sync_pack_to_open_plans(
    db: AsyncSession,
    *,
    tenant_id: str,
    pack: ClosePack,
    user_id: Optional[str],
    orphan_task_ids: Optional[set[str]] = None,
    orphan_subtask_ids: Optional[set[str]] = None,
) -> dict:
    """Propagate a Close Pack template change into every plan already
    generated from it whose period is still open (current or future).

      * new task templates  -> materialized into those plans (idempotent)
      * new sub-task templates -> added under their parent task
      * deleted templates   -> their still-not-started instances removed
        (pass the freed-up Task / SubTask ids as `orphan_*_ids`)
      * renamed / re-timed templates -> pushed onto not-started instances

    CLOSED periods and any task past NOT_STARTED / UPCOMING are never
    touched. Returns a small counts dict.
    """
    orphan_task_ids = set(orphan_task_ids or [])
    orphan_subtask_ids = set(orphan_subtask_ids or [])

    version = await snapshot_pack(db, tenant_id, pack, user_id)
    task_templates = version.snapshot_json.get("task_templates", [])
    tmpl_by_id = {t["id"]: t for t in task_templates}

    pair_rows = await db.execute(
        select(Plan, Period)
        .join(Period, Plan.period_id == Period.id)
        .where(
            Plan.tenant_id == tenant_id,
            Plan.source_pack_id == pack.id,
            Period.status != PeriodStatus.CLOSED,
        )
        .order_by(Period.start_date)
    )
    pairs = list(pair_rows.all())
    result = {"plans": 0, "tasks_added": 0, "tasks_removed": 0, "tasks_updated": 0}
    if not pairs:
        return result

    current_end = await _current_period_end(db, tenant_id, pairs[0][1])
    added = removed = updated = 0

    # 1. drop instances whose template was just deleted (only if untouched)
    if orphan_task_ids:
        rows = await db.execute(
            select(Task)
            .where(Task.id.in_(orphan_task_ids), Task.tenant_id == tenant_id)
            .options(selectinload(Task.sub_tasks))
        )
        for t in rows.scalars().all():
            if t.status in _SYNCABLE and all(
                s.status in _SYNCABLE for s in t.sub_tasks
            ):
                await db.delete(t)
                removed += 1
    if orphan_subtask_ids:
        rows = await db.execute(
            select(SubTask).where(
                SubTask.id.in_(orphan_subtask_ids), SubTask.tenant_id == tenant_id
            )
        )
        for s in rows.scalars().all():
            if s.status in _SYNCABLE:
                await db.delete(s)
                removed += 1
    await db.flush()

    # 2. per open plan — update existing tasks/sub-tasks IN PLACE first,
    # THEN materialize whatever's still missing. Order matters: a
    # template edit that changes its computed due date (e.g. a different
    # due_day_of_period) must be pushed onto the existing Task before we
    # ask "does a Task for this template+due-date already exist?" —
    # otherwise that lookup only knows the OLD due date, doesn't recognize
    # the existing (not-yet-updated) Task as covering the template, and
    # materialize_plan_for_entity adds a second, duplicate Task for the
    # NEW due date. Sub-tasks have the same ordering for the same reason.
    for plan, period in pairs:
        upcoming = period.start_date > current_end

        existing = await db.execute(
            select(Task)
            .where(Task.plan_id == plan.id, Task.source_template_id.is_not(None))
            .options(selectinload(Task.sub_tasks))
        )
        for t in existing.scalars().all():
            tmpl = tmpl_by_id.get(t.source_template_id)
            if not tmpl:
                continue
            # The task's OWN title/due-date only rewrite while the task
            # itself hasn't been started (_SYNCABLE) — same rule as always.
            # A carried-forward instance already has a deliberate due date
            # and title set by the carry-forward service — don't rewrite it,
            # only keep its sub-tasks in step with the template.
            if t.status in _SYNCABLE and not t.carried_from_label and _apply_template_fields(t, tmpl, period, pack.calendar_type):
                updated += 1
            # Sub-task reconciliation is INTENTIONALLY independent of the
            # parent task's own status: a task sitting at ASSIGNED (someone
            # picked, nobody's actually done anything yet) still needs a
            # newly-added sub-task template to show up under it — each
            # sub-task instance is separately protected by its OWN status
            # inside _reconcile_subtasks. Only a genuinely finished parent
            # (COMPLETED) is skipped, so template edits stop reaching a
            # task once its work is actually done.
            if t.status != TaskStatus.COMPLETED:
                tmpl_freq = _freq(tmpl.get("frequency"))
                a, r = await _reconcile_subtasks(
                    db, tenant_id, t, tmpl.get("sub_task_templates", []), upcoming, period,
                    calendar=pack.calendar_type,
                    parent_is_subperiod=bool(tmpl_freq and tmpl_freq in SUBPERIOD),
                )
                added += a
                removed += r
        # Flush the field/due-date rewrites above so the existence check
        # inside materialize_plan_for_entity (below) sees the up-to-date
        # due dates, not the pre-edit ones.
        await db.flush()

        before = await _count_plan_tasks(db, plan.id)
        await materialize_plan_for_entity(
            db,
            tenant_id=tenant_id,
            pack=pack,
            version_number=version.version_number,
            task_templates=task_templates,
            period=period,
            entity_id=plan.entity_id,
            owner_id=plan.owner_id,
            upcoming=upcoming,
        )
        added += max(0, await _count_plan_tasks(db, plan.id) - before)

        plan.source_pack_version = version.version_number

    await db.flush()
    await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action="PACK_PLANS_SYNCED",
        entity_type="ClosePack",
        entity_id=pack.id,
        meta={
            "pack_version": version.version_number,
            "plans": len(pairs),
            "tasks_added": added,
            "tasks_removed": removed,
            "tasks_updated": updated,
        },
    )
    return {
        "plans": len(pairs),
        "tasks_added": added,
        "tasks_removed": removed,
        "tasks_updated": updated,
    }
