"""add member_assigned_to_id to tasks

Revision ID: 0012_task_member_assignment
Revises: 0011_checkpoint_due_date
Create Date: 2026-07-01

Supports the flexible completion hierarchy for leaf Tasks (no sub-tasks
defined). ``tasks.assigned_to_id`` keeps meaning whatever it already means
in each existing flow (the owning Team Lead for admin/template-assigned
tasks, or the executing Team Member for ad-hoc tasks a Lead creates
directly). ``member_assigned_to_id`` is the new, unambiguous field the
Team Lead uses to hand a leaf task to a specific Team Member for
execution — that person submits the task for review and the Lead
approves/rejects it, mirroring the existing checkpoint approval flow.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0012_task_member_assignment"
down_revision: Union[str, None] = "0011_checkpoint_due_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "member_assigned_to_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tasks_member_assigned", "tasks", ["member_assigned_to_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_member_assigned", table_name="tasks")
    op.drop_column("tasks", "member_assigned_to_id")
