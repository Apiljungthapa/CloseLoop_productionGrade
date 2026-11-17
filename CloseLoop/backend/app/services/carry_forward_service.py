"""
Carry-forward service.

When a period closes:
  1. Mark the period CLOSED
  2. Create the next OPEN period
  3. Generate plans for the next period (using latest pack version per closing plan)
  4. Find incomplete tasks/subtasks in the closing period and build a
     CarryForwardGroup (status=PENDING). Items are copied into the next period
     only after a supervisor approves the group.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, or_
from sqlalchemy.orm import selectinload

from ..models import (
    DueDateType,
    Period,
    PeriodStatus,
    Plan,
    Task,
    SubTask,
    TaskStatus,
    CarryForwardGroup,
    CarryForwardItem,
    CarryForwardStatus,
    ClosePack,
    PlanStatus,
    Priority,
    Role,
    User,
)
from .period_service import get_next_defined_period, get_or_create_config
from .plan_generation_service import generate_plans
from .due_date_rules import roll_to_working_day
from .recurrence import due_within
from ..calendar import nepal_today


def _priority_from_snap(snap: dict) -> Priority:
    raw = snap.get("priority")
    try:
        return Priority(raw)
    except (ValueError, TypeError):
        return Priority.MEDIUM
from .audit_service import log_action


_OPEN_STATUSES = (
    TaskStatus.NOT_STARTED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.REJECTED,
)


def _grace_window_clause(due_date_col, period_end, today):
    """SQL condition: TRUE when this task/sub-task's due date runs PAST its
    own period's end date and that extension hasn't expired yet — i.e. it
    must be EXCLUDED from "this period just closed, sweep everything open
    into carry-forward" logic (Post-Period Working Days).

    Deliberately period-bound-aware (`due_date > period_end`), not keyed
    off `due_date_type`: a NON-recurring "Once per Period" task gets this
    from AFTER_PERIOD_END + post_period_due_days, but a RECURRING task
    (Daily through Annual, any frequency except "Once per Period") gets it
    from N-Day Due Date (`due_days`) instead, which never touches
    due_date_type — it's always left at its WITHIN_PERIOD default even
    though the computed due_date can legitimately land after the period
    ends. Comparing against `period_end` directly covers BOTH mechanisms
    uniformly, so every frequency gets the same "stays attached to its
    original period_id and stays genuinely workable there until it's
    completed or its own due date actually passes" guarantee — closing a
    period must never force such a task into CARRIED_FORWARD or lock its
    plan out from under it while days are still left on the clock.

    Now a legacy guard: due dates are held inside their own period, so no
    newly generated task or sub-task satisfies it. It only still protects a
    row saved before that rule, until it is completed or its date passes."""
    return (due_date_col > period_end) & (due_date_col >= today)


async def _period_plan_ids(db: AsyncSession, tenant_id: str, period_id: str) -> list[str]:
    rows = await db.execute(
        select(Plan.id).where(Plan.tenant_id == tenant_id, Plan.period_id == period_id)
    )
    return [r[0] for r in rows.all()]


async def roll_pack_tasks(
    db: AsyncSession,
    tenant_id: str,
    from_period: Period,
    to_period: Period,
    user_id: Optional[str],
) -> dict:
    """When a period closes, for recurring Close-Pack and recurring custom
    tasks:

      1. Unlock the next period's tasks/subtasks (UPCOMING -> NOT_STARTED) —
         that period is now the running one.
      2. For every still-incomplete recurring task: if the template (or
         custom rule) produced a FRESH instance in the next period, flag
         that instance `carried_from_label` so the Lead sees it rolled
         over. If it did NOT recur into the next period (e.g. an ANNUAL
         task in a non-anniversary period), the old instance is simply
         marked CARRIED_FORWARD in the closing period — it is NOT cloned
         into every intervening period. The next real occurrence is
         materialised fresh by _regenerate_plans_for_next_period /
         extend_recurring_custom_tasks when its period arrives.
    """
    to_plan_ids = await _period_plan_ids(db, tenant_id, to_period.id)
    unlocked = 0
    if to_plan_ids:
        # Unlock only the tasks whose OCCURRENCE genuinely landed in the
        # now-current period (coalesced to due_date for a non-recurring
        # task, which has no separate occurrence). A "scheduled
        # placeholder" (a period-level recurring task in a non-anniversary
        # period) is also UPCOMING but its occurrence points at a future
        # period — it must stay locked. Bounded by occurrence, never by
        # due_date directly: N-Day Due Date can push a real occurrence's
        # due_date past this period's own end.
        occ = func.coalesce(Task.occurrence_date, Task.due_date)
        r1 = await db.execute(
            update(Task)
            .where(
                Task.plan_id.in_(to_plan_ids),
                Task.status == TaskStatus.UPCOMING,
                Task.due_date.is_not(None),
                occ >= to_period.start_date,
                occ <= to_period.end_date,
            )
            .values(status=TaskStatus.NOT_STARTED)
        )
        r2 = await db.execute(
            update(SubTask)
            .where(
                SubTask.task_id.in_(
                    select(Task.id).where(
                        Task.plan_id.in_(to_plan_ids),
                        Task.status == TaskStatus.NOT_STARTED,
                    )
                ),
                SubTask.status == TaskStatus.UPCOMING,
            )
            .values(status=TaskStatus.NOT_STARTED)
        )
        unlocked = (r1.rowcount or 0) + (r2.rowcount or 0)

    from_plan_ids = await _period_plan_ids(db, tenant_id, from_period.id)
    carried_linked = 0
    carried_cloned = 0
    if from_plan_ids:
        today = nepal_today()
        open_q = await db.execute(
            select(Task)
            .where(
                Task.plan_id.in_(from_plan_ids),
                Task.status.in_(_OPEN_STATUSES),
                # only RECURRING tasks roll here — one-off custom tasks use
                # the regular carry-forward group.
                or_(
                    Task.source_template_id.is_not(None),
                    Task.recurring_group_id.is_not(None),
                ),
                # A pack task (once-per-period OR recurring, via N-Day Due
                # Date) still inside its own post-period working window
                # must NOT be swept here — it stays attached to (and
                # workable in) its original period. See _grace_window_clause.
                ~_grace_window_clause(Task.due_date, from_period.end_date, today),
            )
            .options(selectinload(Task.sub_tasks))
        )
        for t in open_q.scalars().all():
            match = [
                Plan.tenant_id == tenant_id,
                Plan.period_id == to_period.id,
                Task.entity_id == t.entity_id,
            ]
            if t.recurring_group_id:
                match.append(Task.recurring_group_id == t.recurring_group_id)
            else:
                match.append(Task.source_template_id == t.source_template_id)
            nxt = (
                await db.execute(
                    select(Task).join(Plan, Task.plan_id == Plan.id).where(*match).limit(1)
                )
            ).scalar_one_or_none()
            if nxt:
                nxt.carried_from_label = from_period.label
                carried_linked += 1
            # else: no occurrence next period — do NOT clone. A sparse
            # recurrence (annual / quarterly-in-monthly) must not spawn an
            # instance in every intervening period.
            t.status = TaskStatus.CARRIED_FORWARD
            for s in t.sub_tasks:
                still_in_window = (
                    s.due_date is not None
                    and s.due_date > from_period.end_date
                    and s.due_date >= today
                )
                if s.status in _OPEN_STATUSES and not still_in_window:
                    s.status = TaskStatus.CARRIED_FORWARD

    await db.flush()
    return {"unlocked": unlocked, "carried_linked": carried_linked, "carried_cloned": carried_cloned}


async def _clone_task_into_period(
    db: AsyncSession, tenant_id: str, task: Task, to_period: Period, from_label: str
) -> None:
    dest_plan = (
        await db.execute(
            select(Plan)
            .where(
                Plan.tenant_id == tenant_id,
                Plan.period_id == to_period.id,
                Plan.entity_id == task.entity_id,
            )
            .order_by(Plan.source_pack_id.is_(None))  # prefer a pack plan
            .limit(1)
        )
    ).scalars().first()
    if not dest_plan:
        # Fall back to the same plan_id lookup as generation would build.
        src_plan = (
            await db.execute(select(Plan).where(Plan.id == task.plan_id))
        ).scalar_one_or_none()
        dest_plan = Plan(
            tenant_id=tenant_id,
            period_id=to_period.id,
            entity_id=task.entity_id,
            owner_id=src_plan.owner_id if src_plan else None,
            source_pack_id=src_plan.source_pack_id if src_plan else None,
            source_pack_version=src_plan.source_pack_version if src_plan else None,
            title=f"{src_plan.title.split(' — ')[0]} — {to_period.label}" if src_plan else to_period.label,
            status=PlanStatus.OPEN,
            due_date=to_period.end_date,
        )
        db.add(dest_plan)
        await db.flush()

    # The clone is due on the same relative day of the NEW period, clamped
    # into it (and rolled to a working day inside it): a carried task never
    # keeps a date from the period it came from, and never leaves the new one.
    new_due = roll_to_working_day(
        due_within(
            to_period.start_date, to_period.end_date,
            (task.due_date - to_period.start_date).days + 1 if task.due_date else None,
        ),
        lo=to_period.start_date, hi=to_period.end_date,
    )
    clone = Task(
        tenant_id=tenant_id,
        plan_id=dest_plan.id,
        entity_id=task.entity_id,
        source_template_id=task.source_template_id,
        recurring_group_id=task.recurring_group_id,
        is_custom=task.is_custom,
        assigned_to_id=task.assigned_to_id,
        member_assigned_to_id=task.member_assigned_to_id,
        title=task.title,
        description=task.description,
        due_date=new_due,
        due_date_type=DueDateType.WITHIN_PERIOD,
        post_period_due_days=0,
        due_time=task.due_time,
        priority=task.priority,
        status=TaskStatus.NOT_STARTED,
        carried_from_label=from_label,
    )
    db.add(clone)
    await db.flush()
    for s in task.sub_tasks:
        if s.status not in _OPEN_STATUSES:
            continue
        s_new_due = new_due
        db.add(
            SubTask(
                tenant_id=tenant_id,
                task_id=clone.id,
                entity_id=s.entity_id,
                source_template_id=s.source_template_id,
                is_custom=s.is_custom,
                assigned_to_id=s.assigned_to_id,
                reviewer_id=s.reviewer_id,
                title=s.title,
                description=s.description,
                due_date=s_new_due,
                due_date_type=DueDateType.WITHIN_PERIOD,
                post_period_due_days=0,
                due_time=s.due_time,
                priority=s.priority,
                status=TaskStatus.NOT_STARTED,
                carried_from_label=from_label,
            )
        )
    await db.flush()


async def _collect_open_items(
    db: AsyncSession, tenant_id: str, period: Period
) -> tuple[list[Task], list[SubTask]]:
    # Only NON-pack plans — pack tasks are rolled by roll_pack_tasks().
    plan_rows = await db.execute(
        select(Plan.id).where(
            Plan.tenant_id == tenant_id,
            Plan.period_id == period.id,
            Plan.source_pack_id.is_(None),
        )
    )
    plan_ids = [r[0] for r in plan_rows.all()]
    if not plan_ids:
        return [], []

    today = nepal_today()
    open_tasks_q = await db.execute(
        select(Task).where(
            Task.tenant_id == tenant_id,
            Task.plan_id.in_(plan_ids),
            Task.status.in_(_OPEN_STATUSES),
            # recurring tasks (template or custom) roll via roll_pack_tasks
            Task.source_template_id.is_(None),
            Task.recurring_group_id.is_(None),
            # Still inside its own post-period working window (once-per-
            # period AFTER_PERIOD_END, or a recurring task's N-Day Due
            # Date pushing its due date past this period's end) -> stays
            # attached to (and workable in) its original period; never
            # swept into carry-forward. See _grace_window_clause.
            ~_grace_window_clause(Task.due_date, period.end_date, today),
        )
    )
    open_tasks = list(open_tasks_q.scalars().all())

    open_subs_q = await db.execute(
        select(SubTask).where(
            SubTask.tenant_id == tenant_id,
            SubTask.task_id.in_([t.id for t in open_tasks])
            if open_tasks
            else SubTask.id.is_(None),
        )
        if open_tasks
        else select(SubTask).where(SubTask.id.is_(None))
    )
    # also fetch all subtasks in this period that are still open even if their parent task is COMPLETED (edge case)
    all_subs_q = await db.execute(
        select(SubTask)
        .join(Task, SubTask.task_id == Task.id)
        .where(
            SubTask.tenant_id == tenant_id,
            Task.plan_id.in_(plan_ids),
            SubTask.status.in_(_OPEN_STATUSES),
            ~_grace_window_clause(SubTask.due_date, period.end_date, today),
        )
        .options(selectinload(SubTask.task))
    )
    open_subs = list(all_subs_q.scalars().all())
    return open_tasks, open_subs


async def _build_carry_forward(
    db: AsyncSession,
    tenant_id: str,
    from_period: Period,
    to_period: Period,
    user_id: Optional[str],
) -> CarryForwardGroup:
    open_tasks, open_subs = await _collect_open_items(db, tenant_id, from_period)

    group = CarryForwardGroup(
        tenant_id=tenant_id,
        from_period_id=from_period.id,
        to_period_id=to_period.id,
        created_by_id=user_id,
        status=CarryForwardStatus.PENDING,
    )
    db.add(group)
    await db.flush()

    # Subtasks already handled below; avoid duplicating tasks that are only "open" because of their subtasks
    sub_parent_ids = {s.task_id for s in open_subs}

    for t in open_tasks:
        if t.id in sub_parent_ids:
            continue  # parent will be implicitly carried as subtasks copy
        item = CarryForwardItem(
            tenant_id=tenant_id,
            group_id=group.id,
            original_plan_id=t.plan_id,
            original_task_id=t.id,
            original_assignee_id=t.assigned_to_id,
            original_entity_id=t.entity_id,
            item_type="TASK",
            title=t.title,
            snapshot_json={
                "title": t.title,
                "description": t.description,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "priority": t.priority.value,
                "status": t.status.value,
            },
        )
        db.add(item)
        # Mark original as CARRIED_FORWARD
        t.status = TaskStatus.CARRIED_FORWARD

    for s in open_subs:
        item = CarryForwardItem(
            tenant_id=tenant_id,
            group_id=group.id,
            original_plan_id=s.task.plan_id if s.task else "",
            original_task_id=s.task_id,
            original_subtask_id=s.id,
            original_assignee_id=s.assigned_to_id,
            original_entity_id=s.entity_id,
            item_type="SUBTASK",
            title=s.title,
            snapshot_json={
                "title": s.title,
                "description": s.description,
                "reviewer_id": s.reviewer_id,
                "due_date": s.due_date.isoformat() if s.due_date else None,
                "priority": s.priority.value,
                "status": s.status.value,
            },
        )
        db.add(item)
        s.status = TaskStatus.CARRIED_FORWARD

    await db.flush()
    await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action="CARRY_FORWARD_CREATED",
        entity_type="CarryForwardGroup",
        entity_id=group.id,
        meta={
            "from_period_id": from_period.id,
            "to_period_id": to_period.id,
            "tasks": len(open_tasks),
            "subtasks": len(open_subs),
        },
    )
    return group


async def _regenerate_plans_for_next_period(
    db: AsyncSession,
    tenant_id: str,
    from_period: Period,
    to_period: Period,
    user_id: Optional[str],
) -> int:
    """Regenerate the next period's Close-Pack plans.

    Scope is every (pack, entity) pair the tenant has EVER generated — from
    plans in ANY period, not just the one just closed. This matters for
    sparse recurrences: an ANNUAL task's pack produces no plan in most
    periods, so keying off the closing period alone would silently drop it
    after the first gap. `_materialize_recurring` still places each task
    only in the period its occurrence actually lands in.

    The owning Team Lead (most recent `Plan.owner_id`) is carried across so
    the new period's plans stay in that Lead's workbench; an owner who is
    no longer a Lead/Admin is dropped rather than failing the whole group.
    The pack's LATEST template snapshot is used. Inactive packs are skipped.
    """
    rows = await db.execute(
        select(Plan.source_pack_id, Plan.entity_id, Plan.owner_id, Plan.created_at)
        .join(ClosePack, ClosePack.id == Plan.source_pack_id)
        .where(
            Plan.tenant_id == tenant_id,
            Plan.source_pack_id.is_not(None),
            ClosePack.active.is_(True),
        )
        .order_by(Plan.created_at)
    )
    # latest owner wins per (pack, entity)
    raw_map: dict[tuple[str, str], Optional[str]] = {}
    for pack_id, entity_id, owner_id, _created in rows.all():
        raw_map[(pack_id, entity_id)] = owner_id
    raw = [(p, e, o) for (p, e), o in raw_map.items()]

    # Which of the referenced owners are still valid plan owners?
    owner_ids = {oid for _, _, oid in raw if oid}
    valid_owners: set[str] = set()
    if owner_ids:
        vo = await db.execute(
            select(User.id).where(
                User.tenant_id == tenant_id,
                User.id.in_(owner_ids),
                User.role.in_([Role.TEAM_LEAD, Role.SUPER_ADMIN]),
            )
        )
        valid_owners = {uid for (uid,) in vo.all()}

    # group by (pack_id, owner_id) -> set of entity_ids
    grouped: dict[tuple[str, Optional[str]], set[str]] = {}
    for pack_id, entity_id, owner_id in raw:
        key_owner = owner_id if owner_id in valid_owners else None
        grouped.setdefault((pack_id, key_owner), set()).add(entity_id)

    total = 0
    for (pack_id, owner_id), ents in grouped.items():
        try:
            created = await generate_plans(
                db,
                tenant_id=tenant_id,
                pack_id=pack_id,
                period_id=to_period.id,
                entity_ids=list(ents),
                user_id=user_id,
                owner_id=owner_id,
            )
            total += len(created)
        except ValueError:
            continue
    return total


async def _plan_has_open_grace_window_item(
    db: AsyncSession, tenant_id: str, plan_id: str, period_end, today
) -> bool:
    """True if this plan still holds a task or sub-task whose due date runs
    past `period_end` and hasn't expired yet — i.e. still inside its post-
    period working window (once-per-period AFTER_PERIOD_END, or a
    recurring task's N-Day Due Date) — that isn't finished yet. Closing the
    PLAN would otherwise lock every mutating endpoint (submit-for-review,
    edit, assign — they all gate on `plan.status == CLOSED`) out from under
    an item that's explicitly still allowed extra days to complete —
    directly violating "the task must remain attached to its original
    period_id [and stays workable there] even when its due date is after
    the period end" (services/due_date_rules.py)."""
    unfinished = (TaskStatus.COMPLETED, TaskStatus.CARRIED_FORWARD)
    t = (
        await db.execute(
            select(Task.id).where(
                Task.tenant_id == tenant_id,
                Task.plan_id == plan_id,
                Task.status.notin_(unfinished),
                _grace_window_clause(Task.due_date, period_end, today),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if t:
        return True
    s = (
        await db.execute(
            select(SubTask.id)
            .join(Task, SubTask.task_id == Task.id)
            .where(
                SubTask.tenant_id == tenant_id,
                Task.plan_id == plan_id,
                SubTask.status.notin_(unfinished),
                _grace_window_clause(SubTask.due_date, period_end, today),
            ).limit(1)
        )
    ).scalar_one_or_none()
    return s is not None


async def close_period(
    db: AsyncSession, tenant_id: str, period: Period, user_id: Optional[str]
) -> tuple[Period, Period, int, int]:
    if period.status == PeriodStatus.CLOSED:
        raise ValueError("Period already closed")

    # 1. mark current closed
    period.status = PeriodStatus.CLOSED
    period.closed_at = datetime.now(timezone.utc)
    period.closed_by_id = user_id

    # close all OPEN/IN_PROGRESS plans in the period — EXCEPT one still
    # holding a task/sub-task inside its own AFTER_PERIOD_END window: that
    # plan stays open (and its item stays genuinely workable, not just
    # theoretically "attached") until the item is completed or its own
    # due date actually passes — the period itself still closes on schedule.
    today = nepal_today()
    plans_q = await db.execute(
        select(Plan).where(
            Plan.tenant_id == tenant_id,
            Plan.period_id == period.id,
        )
    )
    for p in plans_q.scalars().all():
        if p.status != PlanStatus.CLOSED:
            if await _plan_has_open_grace_window_item(db, tenant_id, p.id, period.end_date, today):
                continue
            p.status = PlanStatus.CLOSED

    # 2. locate the pre-defined next period and shift status only
    next_period = await get_next_defined_period(db, tenant_id, period)
    if not next_period:
        raise ValueError("No next predefined period found for the closing period")
    next_period.status = PeriodStatus.OPEN
    next_period.closed_at = None
    next_period.closed_by_id = None

    # 3. top up Close-Pack plans across the rolling horizon (idempotent —
    #    reuses the periods already materialized, adds any new tail period)
    plans_created = await _regenerate_plans_for_next_period(
        db, tenant_id, period, next_period, user_id
    )

    # 3a. extend custom recurring tasks (Lead "Add Task" with a frequency)
    #     into the same window — the pack-template path above doesn't cover
    #     them since they have no template, only a stored recurrence_rule.
    try:
        from .plan_generation_service import extend_recurring_custom_tasks

        await extend_recurring_custom_tasks(
            db, tenant_id=tenant_id, start_period=next_period
        )
    except Exception:  # noqa: BLE001 — never block a period close
        import logging

        logging.getLogger(__name__).exception(
            "extend_recurring_custom_tasks failed for tenant=%s period=%s",
            tenant_id,
            period.id,
        )

    # 3b. roll Close-Pack tasks: unlock the now-current period, carry every
    #     still-incomplete task forward (flag the recurring instance, or
    #     clone a non-recurring one as pending).
    pack_roll = await roll_pack_tasks(db, tenant_id, period, next_period, user_id)

    # 4. carry-forward group — only for NON-pack (ad-hoc) plans, and only if enabled
    cfg = await get_or_create_config(db, tenant_id)
    cf_items = 0
    if cfg.carry_forward_enabled:
        group = await _build_carry_forward(db, tenant_id, period, next_period, user_id)
        cf_items = (
            await db.execute(
                select(func.count(CarryForwardItem.id)).where(
                    CarryForwardItem.group_id == group.id
                )
            )
        ).scalar() or 0

    await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action="PERIOD_CLOSED",
        entity_type="Period",
        entity_id=period.id,
        meta={
            "next_period_id": next_period.id,
            "plans_generated": plans_created,
            "carry_forward_items": cf_items,
            "pack_tasks_unlocked": pack_roll["unlocked"],
            "pack_tasks_carried": pack_roll["carried_linked"] + pack_roll["carried_cloned"],
        },
    )

    # 5. Report Engine event hook (Task 3 §3.3)
    # Auto-generate the Period Close Completion Report, persist it as
    # an immutable ReportSnapshot, and notify Super Admins + Team Lead
    # plan-owners. Isolated in a try/except: a snapshot or notification
    # failure must NOT block the period close itself.
    try:
        # Local import keeps services/carry_forward_service.py free of
        # any hard dependency on the Report Engine — if report_event_service
        # is removed or refactored, this module still imports cleanly.
        from .report_event_service import emit_period_close_completion_event

        await emit_period_close_completion_event(
            db,
            tenant_id=tenant_id,
            period=period,
            generated_by_id=user_id,
        )
    except Exception:  # noqa: BLE001 — defensive: never block close
        import logging

        logging.getLogger(__name__).exception(
            "Period close report snapshot failed for tenant=%s period=%s — period close itself succeeded",
            tenant_id,
            period.id,
        )

    return period, next_period, plans_created, cf_items


async def apply_carry_forward_decision(
    db: AsyncSession,
    tenant_id: str,
    group: CarryForwardGroup,
    decision: str,
    user_id: Optional[str],
    notes: Optional[str] = None,
    item_ids: Optional[List[str]] = None,
) -> CarryForwardGroup:
    decision_u = decision.upper().strip()
    if decision_u not in ("APPROVE", "REJECT"):
        raise ValueError("decision must be APPROVE or REJECT")

    items_q = await db.execute(
        select(CarryForwardItem).where(
            CarryForwardItem.group_id == group.id,
            CarryForwardItem.tenant_id == tenant_id,
        )
    )
    items = list(items_q.scalars().all())

    target_ids = set(item_ids) if item_ids else {i.id for i in items}

    if decision_u == "REJECT":
        for it in items:
            if it.id in target_ids:
                it.approved = False
        group.status = CarryForwardStatus.REJECTED
    else:
        # copy approved items into next period
        next_period_id = group.to_period_id
        if not next_period_id:
            raise ValueError("CarryForwardGroup is missing to_period_id")

        for it in items:
            if it.id not in target_ids:
                it.approved = False
                continue
            it.approved = True
            await _copy_item_to_next_period(db, tenant_id, it, next_period_id)
        group.status = CarryForwardStatus.APPROVED

    group.notes = notes
    group.reviewed_at = datetime.now(timezone.utc)
    group.reviewed_by_id = user_id

    await log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=f"CARRY_FORWARD_{decision_u}D",
        entity_type="CarryForwardGroup",
        entity_id=group.id,
        meta={"items": len(target_ids), "notes": notes},
    )
    await db.flush()
    return group


async def _copy_item_to_next_period(
    db: AsyncSession,
    tenant_id: str,
    item: CarryForwardItem,
    to_period_id: str,
) -> None:
    """Copy a carried-forward TASK or SUBTASK into the next period.

    Strategy: find/create a destination Plan in the new period for the same
    entity (reusing the most-recently-created plan in that period for that
    entity) and attach the cloned task or subtask there. Carry-forward items
    are grouped under a labeled "Prior Period Carry-Forward" plan/parent so
    they are visually distinguishable in the new period.

    Operational due_dates are reset within the new period bounds — defaulting
    to `to_period.start_date + 7 days` (clamped to `to_period.end_date`).
    """
    # Load destination period so we can reset due_dates within bounds.
    to_period_row = await db.execute(select(Period).where(Period.id == to_period_id))
    to_period = to_period_row.scalar_one_or_none()
    default_due = None
    if to_period:
        candidate = to_period.start_date + timedelta(days=7)
        default_due = (
            candidate if candidate <= to_period.end_date else to_period.end_date
        )

    plan_q = await db.execute(
        select(Plan)
        .where(
            Plan.tenant_id == tenant_id,
            Plan.period_id == to_period_id,
            Plan.entity_id == item.original_entity_id,
            Plan.title == "Prior Period Carry-Forward",
        )
        .order_by(Plan.created_at)
        .limit(1)
    )
    dest_plan = plan_q.scalar_one_or_none()
    if not dest_plan:
        # Build a dedicated, labeled bucket plan for carry-forward items so
        # they are obvious to leads in the new period.
        dest_plan = Plan(
            tenant_id=tenant_id,
            period_id=to_period_id,
            entity_id=item.original_entity_id,
            title="Prior Period Carry-Forward",
            description="Items carried forward from the previous period",
            status=PlanStatus.OPEN,
        )
        db.add(dest_plan)
        await db.flush()

    snap = item.snapshot_json or {}
    if item.item_type == "TASK":
        new_task = Task(
            tenant_id=tenant_id,
            plan_id=dest_plan.id,
            entity_id=item.original_entity_id,
            assigned_to_id=item.original_assignee_id,
            title=snap.get("title", item.title),
            description=snap.get("description"),
            due_date=default_due,
            priority=_priority_from_snap(snap),
            status=TaskStatus.NOT_STARTED,
        )
        db.add(new_task)
        await db.flush()
        item.copied_to_task_id = new_task.id

    elif item.item_type == "SUBTASK":
        # We need a parent task in the new plan; reuse a single labeled parent
        # so all carried-forward subtasks group together.
        parent_q = await db.execute(
            select(Task).where(
                Task.tenant_id == tenant_id,
                Task.plan_id == dest_plan.id,
                Task.title == "Prior Period Carry-Forward",
            )
        )
        parent = parent_q.scalar_one_or_none()
        if not parent:
            parent = Task(
                tenant_id=tenant_id,
                plan_id=dest_plan.id,
                entity_id=item.original_entity_id,
                title="Prior Period Carry-Forward",
                description="Holding task for carried-forward subtasks",
                due_date=default_due,
                status=TaskStatus.NOT_STARTED,
            )
            db.add(parent)
            await db.flush()

        new_sub = SubTask(
            tenant_id=tenant_id,
            task_id=parent.id,
            entity_id=item.original_entity_id,
            assigned_to_id=item.original_assignee_id,
            reviewer_id=snap.get("reviewer_id"),
            title=snap.get("title", item.title),
            description=snap.get("description"),
            due_date=default_due,
            priority=_priority_from_snap(snap),
            status=TaskStatus.NOT_STARTED,
        )
        db.add(new_sub)
        await db.flush()
        item.copied_to_subtask_id = new_sub.id
