"""Add BS_MONTH, BS_QUARTER, BS_HALF_YEAR and CALENDAR_HALF_YEAR to the
period_cadence enum — Nepali (Bikram Sambat) fiscal-calendar period
generation, see app/calendar/.

Revision ID: 0031_bs_period_cadences
Revises: 0030_subtask_multi_assign
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0031_bs_period_cadences"
down_revision: Union[str, None] = "0030_subtask_multi_assign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE period_cadence ADD VALUE IF NOT EXISTS 'CALENDAR_HALF_YEAR'")
    op.execute("ALTER TYPE period_cadence ADD VALUE IF NOT EXISTS 'BS_MONTH'")
    op.execute("ALTER TYPE period_cadence ADD VALUE IF NOT EXISTS 'BS_QUARTER'")
    op.execute("ALTER TYPE period_cadence ADD VALUE IF NOT EXISTS 'BS_HALF_YEAR'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in place.
    pass
