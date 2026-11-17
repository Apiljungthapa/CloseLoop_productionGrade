"""Close Pack task recurrence:

  * new enum recurrence_frequency (WEEKLY / MONTHLY / QUARTERLY /
    SEMI_ANNUAL / ANNUAL)
  * task_templates.frequency — NULL = every period
  * tasks.carried_from_label / sub_tasks.carried_from_label — set when a
    still-incomplete recurring task is carried into the next period.

(task_status already has 'UPCOMING' from 0035.)

Revision ID: 0038_task_frequency
Revises: 0037_drop_pack_lifecycle
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0038_task_frequency"
down_revision: Union[str, None] = "0037_drop_pack_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALUES = ("WEEKLY", "MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL")


def upgrade() -> None:
    postgresql.ENUM(*_VALUES, name="recurrence_frequency").create(
        op.get_bind(), checkfirst=True
    )
    op.add_column(
        "task_templates",
        sa.Column(
            "frequency",
            postgresql.ENUM(*_VALUES, name="recurrence_frequency", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("tasks", sa.Column("carried_from_label", sa.String(length=100), nullable=True))
    op.add_column("sub_tasks", sa.Column("carried_from_label", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("sub_tasks", "carried_from_label")
    op.drop_column("tasks", "carried_from_label")
    op.drop_column("task_templates", "frequency")
    op.execute("DROP TYPE IF EXISTS recurrence_frequency")
