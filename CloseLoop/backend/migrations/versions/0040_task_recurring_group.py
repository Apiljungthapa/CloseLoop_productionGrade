"""tasks.recurring_group_id — shared id across every period-instance of one
recurring CUSTOM task a Lead/Admin created via "Add Task" with a frequency.

Revision ID: 0040_task_recurring_group
Revises: 0039_task_subperiod_recurrence
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0040_task_recurring_group"
down_revision: Union[str, None] = "0039_task_subperiod_recurrence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("recurring_group_id", sa.String(length=64), nullable=True))
    op.create_index("ix_tasks_recurring_group", "tasks", ["recurring_group_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_recurring_group", table_name="tasks")
    op.drop_column("tasks", "recurring_group_id")
