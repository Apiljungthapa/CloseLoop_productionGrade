"""Add CALENDAR_YEAR and BS_YEAR to the period_cadence enum — annual/yearly
period generation (English calendar year and Nepali fiscal year), on top of
the month/quarter/half-year cadences added in 0031.

Revision ID: 0032_annual_period_cadences
Revises: 0031_bs_period_cadences
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0032_annual_period_cadences"
down_revision: Union[str, None] = "0031_bs_period_cadences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE period_cadence ADD VALUE IF NOT EXISTS 'CALENDAR_YEAR'")
    op.execute("ALTER TYPE period_cadence ADD VALUE IF NOT EXISTS 'BS_YEAR'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in place.
    pass
