"""task_templates.recurrence_start / recurrence_end — a recurring task is
anchored to a start date; every occurrence is computed forward from there
and dropped into whichever period contains it.

Revision ID: 0041_task_recurrence_anchor
Revises: 0040_task_recurring_group
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0041_task_recurrence_anchor"
down_revision: Union[str, None] = "0040_task_recurring_group"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task_templates", sa.Column("recurrence_start", sa.Date(), nullable=True))
    op.add_column("task_templates", sa.Column("recurrence_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_templates", "recurrence_end")
    op.drop_column("task_templates", "recurrence_start")
