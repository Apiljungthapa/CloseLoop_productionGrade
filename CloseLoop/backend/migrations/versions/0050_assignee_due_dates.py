"""Per-assignee deadlines on multi-assignee tasks/sub-tasks.

Revision ID: 0050_assignee_due_dates
Revises: 0049_subtask_template_frequency
Create Date: 2026-09-15

A task/sub-task shared across several people (whole-entity or a
hand-picked multi-assign) previously had one due date for everyone.
Some of that shared work is sequential in practice (one person's part
feeds into another's), so the assigner can now give each assignee their
own optional deadline, always on or before the task's/sub-task's own
due date (enforced in routers/tasks.py + routers/subtasks.py, not at
the DB layer — this column is a plain nullable Date).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0050_assignee_due_dates"
down_revision: Union[str, None] = "0049_subtask_template_frequency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_entity_assignees", sa.Column("due_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "sub_task_entity_assignees", sa.Column("due_date", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sub_task_entity_assignees", "due_date")
    op.drop_column("task_entity_assignees", "due_date")
