"""
Report access service — the SINGLE place that resolves "what can this
caller see in a report?"

Implements the rigid RBAC matrix from the Report Engine spec
(Task 3 §1):

    Super Admin   :  all entities, all groups, all users in tenant
    Group Manager :  entities in assigned groups + nested descendant groups
    Team Lead     :  entities they are assigned to + entities of plans they
                     own (Plan.owner_id == me.id) + team members assigned
                     under their plans/tasks/subtasks
    Team Member   :  THEMSELVES ONLY — and only for the
                     User Workload and Progress Report (2.3)

The router layer should use these helpers to either (a) short-circuit a
403, or (b) intersect the report query with the caller's allowed
entity-set / user-set. Tenant isolation is enforced unconditionally —
every helper requires a tenant_id and refuses cross-tenant lookups.
"""

from __future__ import annotations

from typing import Optional, Set

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models import (
    Plan,
    Role,
    SubTask,
    Task,
    TaskEntityAssignee,
    User,
)
from .access_service import get_accessible_entity_ids


# ============================================================
# Report type identifiers (single source of truth)
# ============================================================
REPORT_PERIOD_CLOSE_COMPLETION = "period_close_completion"
REPORT_OVERDUE_TASK = "overdue_task"
REPORT_USER_WORKLOAD = "user_workload"
REPORT_YEAR_END_ROLLUP = "year_end_rollup"
REPORT_TIME_ENTRY = "time_entry"
# Live task-by-task status of the currently-running period's Close-Pack
# plans (defaults to the period covering today; a specific period_id may
# be passed to see a past/future period). This is the report that used to
# live inline on the Close Packs page.
REPORT_CURRENT_PERIOD_STATUS = "current_period_status"
# Report Studio: KPI / chart / findings reports (services/report_studio.py). Leads and Super Admins
# only, scoped to their entities; a Team Member is never allowed (they have their own scorecard).
REPORT_STUDIO = "report_studio"

ALL_REPORT_TYPES: frozenset[str] = frozenset(
    {
        REPORT_PERIOD_CLOSE_COMPLETION,
        REPORT_OVERDUE_TASK,
        REPORT_USER_WORKLOAD,
        REPORT_YEAR_END_ROLLUP,
        REPORT_TIME_ENTRY,
        REPORT_CURRENT_PERIOD_STATUS,
        REPORT_STUDIO,
    }
)

# Reports a Team Member is allowed to run at all (only their own scorecard).
MEMBER_ALLOWED_REPORTS: frozenset[str] = frozenset({REPORT_USER_WORKLOAD})


# ============================================================
# Role + report-type entry gate
# ============================================================
def ensure_can_run_report(user: User, report_type: str) -> None:
    """
    Raise 403 if the user's role is not permitted to run this report at all.

    Visibility further inside the report (which entities, which users) is
    enforced by `resolve_report_entity_scope` / `resolve_report_user_scope`.
    """
    if not user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required for reports",
        )
    
    if report_type not in ALL_REPORT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown report type: {report_type}",
        )
    if user.role == Role.PLATFORM_OWNER:
        # Platform owner has no tenant scope; reports are per-tenant.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform Owner has no tenant context for reports",
        )
    
    if user.role == Role.TEAM_MEMBER and report_type not in MEMBER_ALLOWED_REPORTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Team Members may only run their own User Workload and Progress Report"
            ),
        )


# ============================================================
# Entity scope resolution
# ============================================================
async def resolve_report_entity_scope(
    db: AsyncSession, user: User
) -> Optional[Set[str]]:
    """
    Return the set of entity_ids this user may include in any report,
    or ``None`` if the user has unrestricted access within their tenant.

    `None` is the "no filter" sentinel — used for SUPER_ADMIN so callers
    can skip an unnecessary `WHERE entity_id IN (...)` clause.

    Returns an empty set when the user has zero accessible entities (in
    which case all reports must return zero rows, NOT a 403; the
    caller decided to *attempt* the report).
    """
    if not user.tenant_id:
        return set()
    if user.role == Role.SUPER_ADMIN:
        return None  # unrestricted within tenant
    if user.role == Role.PLATFORM_OWNER:
        return set()  # no tenant scope
    # TEAM_LEAD, TEAM_MEMBER → use the standard access map.
    base_scope: Set[str] = await get_accessible_entity_ids(db, user)
    # Team Leads ALSO see entities of plans they explicitly own (the
    # plan-ownership layer added in Task 2 trumps strict entity scope).
    if user.role == Role.TEAM_LEAD:
        owned = await db.execute(
            select(Plan.entity_id).where(
                Plan.tenant_id == user.tenant_id, Plan.owner_id == user.id
            )
        )
        for (ent_id,) in owned.all():
            if ent_id:
                base_scope.add(ent_id)
    return base_scope


# ============================================================
# User scope resolution (for Time Entry / User Workload reports)
# ============================================================
async def resolve_report_user_scope(db: AsyncSession, user: User) -> Optional[Set[str]]:
    """
    Return the set of user_ids whose data this caller may include, or
    ``None`` for unrestricted (Super Admin).

    Rules:
      * SUPER_ADMIN     → None (unrestricted in tenant)
      * TEAM_LEAD       → themselves + any user assigned to a task /
                          subtask under a plan they own OR can edit (task
                          owner, task executor, whole-entity assignee,
                          sub-task assignee or reviewer)
      * TEAM_MEMBER     → {self}
    """
    if not user.tenant_id:
        return set()
    if user.role == Role.SUPER_ADMIN:
        return None
    if user.role == Role.PLATFORM_OWNER:
        return set()
    if user.role == Role.TEAM_MEMBER:
        return {user.id}

    # TEAM_LEAD: derive from the entity scope.
    entity_scope = await resolve_report_entity_scope(db, user)
    user_ids: Set[str] = {user.id}
    if entity_scope is None:
        return None
    if not entity_scope:
        return user_ids

    # All task owners (the Lead) AND executors (the Team Member a leaf task
    # was handed to) in this entity scope
    rows = await db.execute(
        select(Task.assigned_to_id, Task.member_assigned_to_id).where(
            Task.tenant_id == user.tenant_id,
            Task.entity_id.in_(entity_scope),
        )
    )
    for assigned_to, member in rows.all():
        if assigned_to:
            user_ids.add(assigned_to)
        if member:
            user_ids.add(member)

    # Members a whole-entity task was fanned out to
    fanout_rows = await db.execute(
        select(TaskEntityAssignee.user_id)
        .join(Task, TaskEntityAssignee.task_id == Task.id)
        .where(
            TaskEntityAssignee.tenant_id == user.tenant_id,
            Task.tenant_id == user.tenant_id,
            Task.entity_id.in_(entity_scope),
        )
    )
    for (uid,) in fanout_rows.all():
        if uid:
            user_ids.add(uid)

    # All subtask assignees + reviewers in this entity scope
    sub_rows = await db.execute(
        select(SubTask.assigned_to_id, SubTask.reviewer_id).where(
            SubTask.tenant_id == user.tenant_id,
            SubTask.entity_id.in_(entity_scope),
        )
    )
    for assigned_to, reviewer in sub_rows.all():
        if assigned_to:
            user_ids.add(assigned_to)
        if reviewer:
            user_ids.add(reviewer)

    return user_ids


# ============================================================
# Convenience guards
# ============================================================
async def ensure_can_view_entity(db: AsyncSession, user: User, entity_id: str) -> None:
    scope = await resolve_report_entity_scope(db, user)
    if scope is not None and entity_id not in scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Entity not in your report scope",
        )


async def ensure_can_view_user(
    db: AsyncSession, viewer: User, target_user_id: str
) -> None:
    scope = await resolve_report_user_scope(db, viewer)
    if scope is not None and target_user_id not in scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target user not in your report scope",
        )
