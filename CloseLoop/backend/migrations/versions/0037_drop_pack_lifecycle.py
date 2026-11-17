"""Revert the Close Pack multi-period *lifecycle* (0036) — the pack no longer
carries any dates. Each Task/SubTask carries its own due date instead, and a
sub-task's due date can never be later than its parent task's (enforced in
routers/close_packs.py).

Kept from 0036: the recurring_task_* tables stay dropped — that feature is
gone for good.
Not reverted: the value 'UPCOMING' added to the task_status enum in 0035 —
PostgreSQL can't remove an enum value in place; it is simply unused.

Revision ID: 0037_drop_pack_lifecycle
Revises: 0036_close_pack_lifecycle
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0037_drop_pack_lifecycle"
down_revision: Union[str, None] = "0036_close_pack_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("fk_close_packs_current_period", "close_packs", type_="foreignkey")
    for col in ("current_period_id", "cycle_number", "period_label", "period_end", "period_start", "lifecycle"):
        op.drop_column("close_packs", col)

    op.drop_index("ix_periods_close_pack", table_name="periods")
    op.drop_constraint("fk_periods_close_pack", "periods", type_="foreignkey")
    op.drop_column("periods", "cycle_number")
    op.drop_column("periods", "close_pack_id")

    for table in ("task_templates", "sub_task_templates"):
        op.drop_column(table, "active_from")
        op.drop_column(table, "active_until")

    for table in ("tasks", "sub_tasks"):
        op.drop_column(table, "carried_from_label")
        op.drop_column(table, "carry_forward_count")
        op.drop_column(table, "active_until")
        op.drop_column(table, "active_from")

    op.execute("DROP TYPE IF EXISTS close_pack_lifecycle")


def downgrade() -> None:
    postgresql.ENUM(
        "DRAFT", "ACTIVE", "COMPLETED", name="close_pack_lifecycle"
    ).create(op.get_bind(), checkfirst=True)
    _lc = postgresql.ENUM(
        "DRAFT", "ACTIVE", "COMPLETED", name="close_pack_lifecycle", create_type=False
    )
    op.add_column("close_packs", sa.Column("lifecycle", _lc, nullable=False, server_default="DRAFT"))
    op.add_column("close_packs", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("close_packs", sa.Column("period_end", sa.Date(), nullable=True))
    op.add_column("close_packs", sa.Column("period_label", sa.String(length=100), nullable=True))
    op.add_column("close_packs", sa.Column("cycle_number", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("close_packs", sa.Column("current_period_id", sa.String(length=64), nullable=True))
    op.create_foreign_key("fk_close_packs_current_period", "close_packs", "periods", ["current_period_id"], ["id"], ondelete="SET NULL")

    op.add_column("periods", sa.Column("close_pack_id", sa.String(length=64), nullable=True))
    op.add_column("periods", sa.Column("cycle_number", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_periods_close_pack", "periods", "close_packs", ["close_pack_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_periods_close_pack", "periods", ["close_pack_id"])

    for table in ("task_templates", "sub_task_templates"):
        op.add_column(table, sa.Column("active_from", sa.Date(), nullable=True))
        op.add_column(table, sa.Column("active_until", sa.Date(), nullable=True))
    for table in ("tasks", "sub_tasks"):
        op.add_column(table, sa.Column("active_from", sa.Date(), nullable=True))
        op.add_column(table, sa.Column("active_until", sa.Date(), nullable=True))
        op.add_column(table, sa.Column("carry_forward_count", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("carried_from_label", sa.String(length=100), nullable=True))
