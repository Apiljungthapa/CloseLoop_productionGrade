"""Add priority to tasks and sub_tasks.

Revision ID: 0021_add_task_subtask_priority
Revises: 0020_remove_task_reviewer_required
Create Date: 2026-08-31

Lead/Admin sets this at assignment time (LOW/MEDIUM/HIGH). Purely
informational — doesn't gate any workflow, just drives sorting/filtering
on the task board.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_add_task_subtask_priority"
down_revision: Union[str, None] = "0020_remove_task_reviewer_required"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_priority_enum = sa.Enum("LOW", "MEDIUM", "HIGH", name="priority")


def upgrade() -> None:
    _priority_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "tasks",
        sa.Column(
            "priority",
            _priority_enum,
            nullable=False,
            server_default="MEDIUM",
        ),
    )
    op.add_column(
        "sub_tasks",
        sa.Column(
            "priority",
            _priority_enum,
            nullable=False,
            server_default="MEDIUM",
        ),
    )


def downgrade() -> None:
    op.drop_column("sub_tasks", "priority")
    op.drop_column("tasks", "priority")
    _priority_enum.drop(op.get_bind(), checkfirst=True)
