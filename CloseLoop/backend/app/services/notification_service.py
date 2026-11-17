"""Notification service for period transitions and work approvals.

Sends notifications to Team Leads and other stakeholders when:
  - A period is about to close
  - A new period opens with plans generated
  - Plans are assigned to them
  - Work is submitted for review
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models import (
    Notification,
    User,
    Role,
    Period,
    Plan,
    Task,
)
from ..websocket import manager


async def _create_and_push(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    type: str,
    message: str,
    link: Optional[str] = None,
) -> Notification:
    """Create a notification row and push it to the user's live socket(s)
    (if connected) so the bell updates without a page refresh."""
    notification = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        type=type,
        message=message,
        link=link,
    )
    db.add(notification)
    await db.flush()
    await manager.send_user(
        user_id,
        "notification",
        {
            "id": notification.id,
            "type": notification.type,
            "message": notification.message,
            "link": notification.link,
        },
    )
    return notification


async def notify_team_leads_period_closed(
    db: AsyncSession,
    tenant_id: str,
    closed_period: Period,
    next_period: Period,
    plans_generated: int,
) -> None:
    """Notify all TEAM_LEAD users that a period closed and new one opened."""
    # Get all TEAM_LEAD users in this tenant
    result = await db.execute(
        select(User).where(
            User.tenant_id == tenant_id,
            User.role == Role.TEAM_LEAD,
        )
    )
    team_leads = result.scalars().all()

    for lead in team_leads:
        # Check if they have plans in the new period
        plan_count_result = await db.execute(
            select(Plan).where(
                Plan.tenant_id == tenant_id,
                Plan.period_id == next_period.id,
            )
        )
        lead_plans = plan_count_result.scalars().all()

        if lead_plans:  # Only notify if there are plans
            await _create_and_push(
                db,
                tenant_id=tenant_id,
                user_id=lead.id,
                type="PERIOD_TRANSITION",
                message=(
                    f"Period {closed_period.label} has closed. "
                    f"Period {next_period.label} is now open with {plans_generated} "
                    f"plan(s) ready for your team."
                ),
                link=f"/periods/{next_period.id}",
            )

    await db.flush()


async def notify_team_lead_plan_assigned(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    plan: Plan,
) -> None:
    """Notify a TEAM_LEAD that a plan has been assigned to them."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="PLAN_ASSIGNED",
        message=f"Plan '{plan.title}' has been assigned to you for period {plan.period_id}.",
        link=f"/plans/{plan.id}",
    )


async def notify_user_task_assigned(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    task_id: str,
    task_title: str,
) -> None:
    """Notify a user (Team Lead or Team Member) that a task has been
    assigned to them."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="TASK_ASSIGNED",
        message=f"New task assigned to you: '{task_title}'.",
        link=f"/tasks/{task_id}",
    )


async def notify_user_subtask_assigned(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    subtask_id: str,
    subtask_title: str,
) -> None:
    """Notify a user that a sub-task has been assigned to them."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="SUBTASK_ASSIGNED",
        message=f"New task assigned to you: '{subtask_title}'.",
        link=f"/subtasks/{subtask_id}",
    )


async def notify_user_task_submitted_for_review(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    task_id: str,
    task_title: str,
) -> None:
    """Notify the responsible Team Lead that a task was submitted for review."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="TASK_PENDING_REVIEW",
        message=f"Task '{task_title}' has been submitted and is pending your review.",
        link=f"/tasks/{task_id}",
    )


async def notify_user_task_approved(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    task_id: str,
    task_title: str,
) -> None:
    """Notify the submitter that their task was approved."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="TASK_APPROVED",
        message=f"Your task '{task_title}' has been approved.",
        link=f"/tasks/{task_id}",
    )


async def notify_user_task_rejected(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    task_id: str,
    task_title: str,
    rejection_note: Optional[str] = None,
) -> None:
    """Notify the submitter that their task was rejected."""
    message = f"Your task '{task_title}' was rejected."
    if rejection_note:
        message += f"\n\nReason: {rejection_note}"
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="TASK_REJECTED",
        message=message,
        link=f"/tasks/{task_id}",
    )


async def notify_user_subtask_submitted_for_review(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    subtask_id: str,
    subtask_title: str,
) -> None:
    """Notify a reviewer that a subtask has been submitted."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="SUBTASK_PENDING_REVIEW",
        message=f"Subtask '{subtask_title}' has been submitted and is pending your review.",
        link=f"/subtasks/{subtask_id}",
    )


async def notify_user_subtask_approved(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    subtask_id: str,
    subtask_title: str,
) -> None:
    """Notify assignee that their subtask was approved."""
    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="SUBTASK_APPROVED",
        message=f"Your subtask '{subtask_title}' has been approved.",
        link=f"/subtasks/{subtask_id}",
    )


async def notify_user_subtask_rejected(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    subtask_id: str,
    subtask_title: str,
    rejection_note: Optional[str] = None,
) -> None:
    """Notify assignee that their subtask was rejected."""
    message = f"Your subtask '{subtask_title}' was rejected."
    if rejection_note:
        message += f"\n\nReason: {rejection_note}"

    await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="SUBTASK_REJECTED",
        message=message,
        link=f"/subtasks/{subtask_id}",
    )


async def notify_report_ready(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_ids: list[str],
    report_type: str,
    snapshot_id: str,
    message: str,
) -> int:
    """
    Push a REPORT_READY notification to a list of users.

    Used by the Report Engine event-emitter when a period close
    auto-generates the Period Close Completion Report so Super Admins
    and relevant Team Leads can pull the audit-grade snapshot from the
    notifications tray.

    Returns the number of notifications created. Skips duplicates within
    the caller's list (defensive).
    """
    inserted = 0
    seen: set[str] = set()
    for uid in user_ids:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        await _create_and_push(
            db,
            tenant_id=tenant_id,
            user_id=uid,
            type="REPORT_READY",
            message=message,
            link=f"/api/reports/snapshots/{snapshot_id}",
        )
        inserted += 1
    await db.flush()
    return inserted


async def notify_timesheet_reminder(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    message: str,
) -> Notification:
    """In-app (and live-pushed) timesheet reminder. Not committed here."""
    return await _create_and_push(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        type="TIMESHEET_REMINDER",
        message=message,
        link="/hrms-integration",
    )


async def get_notifications(
    db: AsyncSession, tenant_id: str, user_id: str, limit: int = 50
) -> list[Notification]:
    """Get unread notifications for a user."""
    result = await db.execute(
        select(Notification)
        .where(
            Notification.tenant_id == tenant_id,
            Notification.user_id == user_id,
            Notification.read == False,
        )
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def mark_notification_as_read(
    db: AsyncSession, notification_id: str, tenant_id: str, user_id: str
) -> Notification:
    """Mark a notification as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.tenant_id == tenant_id,
            Notification.user_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise ValueError("Notification not found")

    notification.read = True
    await db.flush()
    return notification
