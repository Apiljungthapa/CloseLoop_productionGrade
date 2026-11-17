"""
Plan customization & hierarchical permission service.

Implements the three-tier authorization model for plan-scoped resources:

  * Tier 1 — Admin (SUPER_ADMIN)
      Full structural authority. Generates plans (optionally assigning a
      Plan owner at generation time), (re)assigns Plan Ownership
      afterward, and may edit any plan / task / subtask in their tenant.
      Admin assigns the WHOLE pack (Plan.owner_id) to a Team Lead — not
      individual tasks; see `ensure_admin_task_assignment_allowed` below.

  * Tier 2 — Team Lead (TEAM_LEAD)
      Structural rights ONLY on plans they explicitly own
      (`Plan.owner_id == user.id`). A plan generated but not yet assigned
      to them (or assigned to someone else) is READ-ONLY — they can view
      its full task/subtask tree but cannot add, edit, delete, assign, or
      approve anything on it. Edits are scoped to that plan only — they
      never mutate master Close Pack templates.

  * Tier 3 — Team Member (TEAM_MEMBER)
      Execution only. Can update status / contribute counts / upload
      evidence on items where they are the assignee, BUT cannot change
      structure (title, description, due_date, assignee) and cannot see
      items where the assignee slot is still empty.

The router layer should call the `ensure_*` helpers (which raise
HTTPException 403) before mutating any resource; the `can_*` helpers
return booleans for visibility filtering.

This service NEVER touches master templates, period state, or carry-forward
records — those flows are preserved exactly as documented in
PERIODS_AND_CARRY_FORWARD_FLOW.md.
"""

from __future__ import annotations

from typing import Optional, Set

from fastapi import HTTPException, status
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Plan,
    Role,
    SubTask,
    Task,
    User,
)


# ============================================================
# Role helpers
# ============================================================
ADMIN_ROLES: frozenset[Role] = frozenset({Role.SUPER_ADMIN})


def is_admin(user: User) -> bool:
    """True for tenant-level admins (Tier 1)."""
    return user.role in ADMIN_ROLES


def is_team_lead(user: User) -> bool:
    return user.role == Role.TEAM_LEAD


def is_team_member(user: User) -> bool:
    return user.role == Role.TEAM_MEMBER


# ============================================================
# Plan-level authorization (Tier 1 + Tier 2)
# ============================================================
async def can_lead_edit_plan(
    db: AsyncSession,
    user: User,
    plan: Plan,
    *,
    accessible_entity_ids: Optional[Set[str]] = None,
) -> bool:
    """
    Return True when `user` may perform structural edits on `plan`.

    Allowed when EITHER:
      * user is a tenant admin (SUPER_ADMIN); or
      * user is a TEAM_LEAD and `plan.owner_id == user.id` — ownership is
        the SOLE basis for a lead's edit rights. A plan admin generated
        but hasn't (yet) assigned to this lead — or assigned to a
        different lead — is read-only to them, however their entity
        scope would otherwise resolve.

    Tenant isolation is enforced by callers (which already filter on
    `tenant_id`); this helper assumes the plan belongs to `user.tenant_id`.

    `accessible_entity_ids` is accepted for backwards compatibility but
    unused — entity scope no longer factors into edit rights, only into
    read visibility (see `plans.py::list_plans`/`get_plan`).
    """
    if not user.tenant_id or plan.tenant_id != user.tenant_id:
        return False
    if is_admin(user):
        return True
    if not is_team_lead(user):
        return False
    return bool(plan.owner_id) and plan.owner_id == user.id


async def ensure_lead_edit_plan(
    db: AsyncSession,
    user: User,
    plan: Plan,
    *,
    accessible_entity_ids: Optional[Set[str]] = None,
) -> None:
    """Raise HTTPException(403) when the user may not structurally edit `plan`."""
    allowed = await can_lead_edit_plan(
        db, user, plan, accessible_entity_ids=accessible_entity_ids
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this plan",
        )


def can_assign_plan_owner(user: User) -> bool:
    """Only Tier-1 admins may assign or change Plan ownership."""
    return is_admin(user)


def ensure_can_assign_plan_owner(user: User) -> None:
    if not can_assign_plan_owner(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins may assign plan ownership",
        )


def ensure_admin_task_assignment_allowed(user: User) -> None:
    """
    Admin assigns the WHOLE pack to a Team Lead (`Plan.owner_id`, via plan
    generation or `PATCH /api/plans/{id}/owner`) — not individual tasks.
    Raise 400 when an admin attempts to set a task's `assigned_to_id`
    directly instead. No-op for everyone else — a Team Lead may still set
    `assigned_to_id` on their own tasks (e.g. handing a custom task's
    leaf execution to a Team Member), that flow is unaffected.
    """
    if is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Admins assign whole packs to a Team Lead via plan "
                "ownership (PATCH /api/plans/{id}/owner), not individual "
                "tasks. Assign the plan to a lead — they'll assign work "
                "within it."
            ),
        )


# ============================================================
# Task-level visibility (Tier 3)
# ============================================================
def can_member_see_task(user: User, task: Task) -> bool:
    """
    Team-Member visibility rule for a Task:

      * Items without an assignee are INVISIBLE to members (the lead has
        not yet handed the task off).
      * Items become visible only when `assigned_to_id == user.id`.

    Non-members (admins, leads) are not constrained by this helper —
    callers should branch by role first.
    """
    if task.assigned_to_id is None:
        return False
    return task.assigned_to_id == user.id


def can_member_see_subtask(user: User, subtask: SubTask) -> bool:
    """A member sees a subtask if it is assigned to them (single assignee,
    one of several multi-assignees, or reviewer)."""
    if subtask.assigned_to_id == user.id:
        return True
    if subtask.reviewer_id and subtask.reviewer_id == user.id:
        return True
    if getattr(subtask, "assigned_entity_wide", False):
        try:
            unloaded = "entity_assignees" in sa_inspect(subtask).unloaded
        except Exception:
            unloaded = True
        if not unloaded and any(
            a.user_id == user.id for a in subtask.entity_assignees
        ):
            return True
    return False


# ============================================================
# Member execution-only edit rules (Tier 3)
# ============================================================
# Field-name allowlists describing what a TEAM_MEMBER is permitted to
# modify on each resource. Routers translate the inbound Pydantic update
# payload to a set of "fields the caller is trying to change" and pass
# it through `ensure_member_update_allowed()`.

TASK_MEMBER_ALLOWED_FIELDS: frozenset[str] = frozenset({"status", "tags"})
SUBTASK_MEMBER_ALLOWED_FIELDS: frozenset[str] = frozenset({"status", "tags"})


def ensure_member_update_allowed(
    user: User,
    requested_fields: Set[str],
    allowed_fields: frozenset[str],
    *,
    resource_label: str = "item",
) -> None:
    """
    For TEAM_MEMBER callers: raise 403 if they tried to change anything
    outside `allowed_fields`. No-op for non-members.
    """
    if not is_team_member(user):
        return
    disallowed = requested_fields - allowed_fields
    if disallowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Team Members may not modify {sorted(disallowed)} on a "
                f"{resource_label}; execution-only access"
            ),
        )


def ensure_completion_details(
    notes: Optional[str], minutes: Optional[int]
) -> tuple[str, int]:
    """
    Enforce mandatory Time Taken + Notes on a completion — used wherever
    submitting IMMEDIATELY completes the work with no separate review
    step to catch a missing/blank value later: a Lead/Admin completing
    their own solo-assigned task or sub-task, and every assignee on a
    shared multi-assignee task (mark-done-entity). A plain Team Member
    submitting to a real reviewer keeps these optional, unchanged.
    """
    notes = (notes or "").strip()
    if not notes:
        raise HTTPException(400, "Notes are required to complete this work.")
    if minutes is None or minutes <= 0:
        raise HTTPException(400, "Time taken is required to complete this work.")
    return notes, minutes
