"""tasks.completed_at / sub_tasks.completed_at — the moment an item actually
reached COMPLETED. Lets the UI show "Completed, but N days late" instead of a
bare "Completed" when the work landed after its due date, and gives reports
an accurate on-time signal (previously proxied by updated_at, which drifts
on any later edit).

Backfilled from updated_at for rows already COMPLETED.

Revision ID: 0042_task_completed_at
Revises: 0041_task_recurrence_anchor
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0042_task_completed_at"
down_revision: Union[str, None] = "0041_task_recurrence_anchor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sub_tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE tasks SET completed_at = updated_at "
        "WHERE status = 'COMPLETED' AND completed_at IS NULL"
    )
    op.execute(
        "UPDATE sub_tasks SET completed_at = updated_at "
        "WHERE status = 'COMPLETED' AND completed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("sub_tasks", "completed_at")
    op.drop_column("tasks", "completed_at")
