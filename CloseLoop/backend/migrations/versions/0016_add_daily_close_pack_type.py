"""Add DAILY to the close_pack_type enum.

Revision ID: 0016_add_daily_close_pack_type
Revises: 0015_completion_submission_metadata
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0016_add_daily_close_pack_type"
down_revision: Union[str, None] = "0015_completion_submission_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE close_pack_type ADD VALUE IF NOT EXISTS 'DAILY'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in place.
    pass
