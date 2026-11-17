"""Sub-period Close Pack task recurrence:

  * recurrence_frequency += DAILY, WEEKLY
  * task_templates.interval_count  (every N days / weeks)
  * task_templates.days_of_week    (0=Mon..6=Sun, for WEEKLY)

Revision ID: 0039_task_subperiod_recurrence
Revises: 0038_task_frequency
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0039_task_subperiod_recurrence"
down_revision: Union[str, None] = "0038_task_frequency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE recurrence_frequency ADD VALUE IF NOT EXISTS 'DAILY'")
    op.execute("ALTER TYPE recurrence_frequency ADD VALUE IF NOT EXISTS 'WEEKLY'")
    op.add_column(
        "task_templates",
        sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "task_templates",
        sa.Column("days_of_week", postgresql.ARRAY(sa.Integer()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_templates", "days_of_week")
    op.drop_column("task_templates", "interval_count")
    # enum values DAILY / WEEKLY are left in place (PostgreSQL can't drop them).
