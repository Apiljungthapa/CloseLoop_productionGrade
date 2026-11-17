"""Multi-period Close Pack lifecycle + removal of the standalone recurring-
task feature.

  * close_packs: lifecycle, period_start/end, period_label, cycle_number,
    current_period_id — an Admin edits the window (via /advance) to roll the
    SAME tasks into the next period; nothing is regenerated.
  * periods: close_pack_id (pack-scoped periods, hidden from the tenant
    period schedule) + cycle_number.
  * task_templates / sub_task_templates: replace the recurrence-schedule
    columns with active_from / active_until.
  * tasks / sub_tasks: active_from, active_until, carry_forward_count,
    carried_from_label.
  * DROP the recurring_task_rules / recurring_task_occurrences tree and its
    enums — the multi-period Close Pack replaces it.

Revision ID: 0036_close_pack_lifecycle
Revises: 0035_close_pack_lifecycle_enums
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0036_close_pack_lifecycle"
down_revision: Union[str, None] = "0035_close_pack_lifecycle_enums"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_TABLES = ("task_templates", "sub_task_templates")
_RECURRING_TABLES = (
    "recurring_occurrence_subtask_assignees",
    "recurring_occurrence_subtasks",
    "recurring_occurrence_assignees",
    "recurring_task_occurrences",
    "recurring_task_rules",
)
_LIFECYCLE = postgresql.ENUM(
    "DRAFT", "ACTIVE", "COMPLETED", name="close_pack_lifecycle", create_type=False
)


def upgrade() -> None:
    # ---- close_packs ----
    op.add_column(
        "close_packs",
        sa.Column("lifecycle", _LIFECYCLE, nullable=False, server_default="DRAFT"),
    )
    op.add_column("close_packs", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("close_packs", sa.Column("period_end", sa.Date(), nullable=True))
    op.add_column("close_packs", sa.Column("period_label", sa.String(length=100), nullable=True))
    op.add_column(
        "close_packs",
        sa.Column("cycle_number", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("close_packs", sa.Column("current_period_id", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_close_packs_current_period",
        "close_packs",
        "periods",
        ["current_period_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---- periods ----
    op.add_column("periods", sa.Column("close_pack_id", sa.String(length=64), nullable=True))
    op.add_column("periods", sa.Column("cycle_number", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_periods_close_pack",
        "periods",
        "close_packs",
        ["close_pack_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_periods_close_pack", "periods", ["close_pack_id"])

    # ---- templates: recurrence columns -> active_from / active_until ----
    for table in _TEMPLATE_TABLES:
        op.add_column(table, sa.Column("active_from", sa.Date(), nullable=True))
        op.add_column(table, sa.Column("active_until", sa.Date(), nullable=True))
        for col in (
            "frequency",
            "interval_count",
            "days_of_week",
            "day_of_month",
            "business_days_only",
            "schedule_start_date",
            "schedule_end_date",
        ):
            op.drop_column(table, col)

    # ---- tasks / sub_tasks: lifecycle fields ----
    for table in ("tasks", "sub_tasks"):
        op.add_column(table, sa.Column("active_from", sa.Date(), nullable=True))
        op.add_column(table, sa.Column("active_until", sa.Date(), nullable=True))
        op.add_column(
            table,
            sa.Column("carry_forward_count", sa.Integer(), nullable=False, server_default="0"),
        )
        op.add_column(table, sa.Column("carried_from_label", sa.String(length=100), nullable=True))

    # ---- drop the standalone recurring-task feature ----
    for table in _RECURRING_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for enum_name in ("recurring_occurrence_status", "recurring_rule_status", "recurrence_frequency"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")


def downgrade() -> None:
    # Recreate the recurrence enum the template columns need.
    postgresql.ENUM(
        "DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUALLY", "CUSTOM",
        name="recurrence_frequency",
    ).create(op.get_bind(), checkfirst=True)
    _freq = postgresql.ENUM(
        "DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUALLY", "CUSTOM",
        name="recurrence_frequency", create_type=False,
    )

    for table in ("sub_tasks", "tasks"):
        op.drop_column(table, "carried_from_label")
        op.drop_column(table, "carry_forward_count")
        op.drop_column(table, "active_until")
        op.drop_column(table, "active_from")

    for table in _TEMPLATE_TABLES:
        op.add_column(table, sa.Column("schedule_end_date", sa.Date(), nullable=True))
        op.add_column(table, sa.Column("schedule_start_date", sa.Date(), nullable=True))
        op.add_column(
            table,
            sa.Column("business_days_only", sa.Boolean(), nullable=False, server_default="false"),
        )
        op.add_column(table, sa.Column("day_of_month", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("days_of_week", postgresql.ARRAY(sa.Integer()), nullable=True))
        op.add_column(
            table,
            sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
        )
        op.add_column(table, sa.Column("frequency", _freq, nullable=True))
        op.drop_column(table, "active_until")
        op.drop_column(table, "active_from")

    op.drop_index("ix_periods_close_pack", table_name="periods")
    op.drop_constraint("fk_periods_close_pack", "periods", type_="foreignkey")
    op.drop_column("periods", "cycle_number")
    op.drop_column("periods", "close_pack_id")

    op.drop_constraint("fk_close_packs_current_period", "close_packs", type_="foreignkey")
    for col in ("current_period_id", "cycle_number", "period_label", "period_end", "period_start", "lifecycle"):
        op.drop_column("close_packs", col)
    # recurring_* tables are NOT recreated on downgrade (feature removed).
