"""holidays — the days the platform is closed on top of Saturday/Sunday.

A global (not tenant-scoped) list of public/bank holidays. Due dates generated
by the recurrence engine, Close Pack templates and carry-forward roll to the
next working day past them, the date pickers refuse them, and overdue counts
skip them — exactly as they already do for the weekend. The table starts EMPTY,
so nothing changes until a holiday is entered (Platform Owner's Holidays page,
POST /api/holidays, or a bulk load from a holiday feed).

Revision ID: 0054_holidays
Revises: 0053_task_required_optional
Create Date: 2026-10-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0054_holidays"
down_revision: Union[str, None] = "0053_task_required_optional"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("holidays"):
        return

    op.create_table(
        "holidays",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="MANUAL"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("holiday_date", name="uq_holidays_date"),
    )


def downgrade() -> None:
    op.drop_table("holidays")
