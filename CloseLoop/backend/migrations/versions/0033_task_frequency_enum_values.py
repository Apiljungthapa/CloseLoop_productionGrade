"""Add QUARTERLY and ANNUALLY to the recurrence_frequency enum — needed by
Close Pack task/subtask template frequencies (0034) as well as
RecurringTaskRule. Split into its own migration because a value added via
ALTER TYPE ... ADD VALUE cannot be referenced by a column default in the
same transaction it was added in.

Revision ID: 0033_task_frequency_enum_values
Revises: 0032_annual_period_cadences
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0033_task_frequency_enum_values"
down_revision: Union[str, None] = "0032_annual_period_cadences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE recurrence_frequency ADD VALUE IF NOT EXISTS 'QUARTERLY'")
    op.execute("ALTER TYPE recurrence_frequency ADD VALUE IF NOT EXISTS 'ANNUALLY'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in place.
    pass
