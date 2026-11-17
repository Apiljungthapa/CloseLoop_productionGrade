"""tasks.recurrence_rule — the recurrence definition of a Lead/Admin custom
recurring task ("Add Task" with a frequency), stored on every instance of
the group so the scheduler can extend it forward one period at a time.

Pack-template tasks don't need this (they carry `source_template_id` and
the rule lives on the TaskTemplate). NULL for every non-recurring task.

Introduced with the short generation horizon (current + next period only):
previously custom recurring tasks were materialised 15 periods deep up
front, so no rule needed to be persisted.

Revision ID: 0043_task_recurrence_rule
Revises: 0042_task_completed_at
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0043_task_recurrence_rule"
down_revision: Union[str, None] = "0042_task_completed_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("recurrence_rule", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "recurrence_rule")
