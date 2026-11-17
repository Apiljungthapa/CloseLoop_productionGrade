"""Close Packs rework:

  * ClosePack: replace the fixed `type` (DAILY/WEEKLY/.../ANNUAL) column
    with `calendar_type` (AD | BS) — which calendar every period/date/
    schedule/report generated from this pack follows.
  * TaskTemplate / SubTaskTemplate: add an independent recurrence schedule
    (frequency, interval_count, days_of_week, day_of_month,
    business_days_only, schedule_start_date, schedule_end_date, due_time) —
    same shape as RecurringTaskRule's, reused by
    services/recurring_task_service.py for both. NULL frequency keeps the
    legacy due_day_of_period-relative-to-plan's-period behaviour.
  * Task / SubTask: add `due_time`, populated from the template at
    generation time.

Revision ID: 0034_close_pack_calendar_and_frequency
Revises: 0033_task_frequency_enum_values
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0034_close_pack_calendar_and_frequency"
down_revision: Union[str, None] = "0033_task_frequency_enum_values"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# recurrence_frequency already exists (created by 0026, extended by 0033) —
# reuse it, don't recreate. See the 0027 migration for why postgresql.ENUM
# with create_type=False (not plain sa.Enum) is required here.
_frequency_enum = postgresql.ENUM(
    "DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUALLY", "CUSTOM",
    name="recurrence_frequency",
    create_type=False,
)


def _add_schedule_columns(table: str) -> None:
    op.add_column(table, sa.Column("frequency", _frequency_enum, nullable=True))
    op.add_column(
        table,
        sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        table, sa.Column("days_of_week", postgresql.ARRAY(sa.Integer()), nullable=True)
    )
    op.add_column(table, sa.Column("day_of_month", sa.Integer(), nullable=True))
    op.add_column(
        table,
        sa.Column(
            "business_days_only", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(table, sa.Column("schedule_start_date", sa.Date(), nullable=True))
    op.add_column(table, sa.Column("schedule_end_date", sa.Date(), nullable=True))
    op.add_column(table, sa.Column("due_time", sa.Time(), nullable=True))


def upgrade() -> None:
    # ---- ClosePack: type -> calendar_type ----
    calendar_type_enum = postgresql.ENUM("AD", "BS", name="calendar_type")
    calendar_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "close_packs",
        sa.Column(
            "calendar_type",
            postgresql.ENUM("AD", "BS", name="calendar_type", create_type=False),
            nullable=False,
            server_default="AD",
        ),
    )
    op.drop_column("close_packs", "type")

    # ---- TaskTemplate / SubTaskTemplate: independent schedule ----
    _add_schedule_columns("task_templates")
    _add_schedule_columns("sub_task_templates")

    # ---- Task / SubTask: due_time ----
    op.add_column("tasks", sa.Column("due_time", sa.Time(), nullable=True))
    op.add_column("sub_tasks", sa.Column("due_time", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("sub_tasks", "due_time")
    op.drop_column("tasks", "due_time")

    for table in ("sub_task_templates", "task_templates"):
        op.drop_column(table, "due_time")
        op.drop_column(table, "schedule_end_date")
        op.drop_column(table, "schedule_start_date")
        op.drop_column(table, "business_days_only")
        op.drop_column(table, "day_of_month")
        op.drop_column(table, "days_of_week")
        op.drop_column(table, "interval_count")
        op.drop_column(table, "frequency")

    op.add_column(
        "close_packs",
        sa.Column(
            "type",
            postgresql.ENUM(
                "ANNUAL", "SEMI_ANNUAL", "QUARTERLY", "MONTHLY", "FOUR_WEEK",
                "BI_WEEKLY", "WEEKLY", "DAILY",
                name="close_pack_type",
                create_type=False,
            ),
            nullable=False,
            server_default="MONTHLY",
        ),
    )
    op.drop_column("close_packs", "calendar_type")
    # Leave the calendar_type enum TYPE itself in place (PostgreSQL enum
    # types can't be dropped while other objects might reference them, and
    # nothing else in this migration created objects that would conflict
    # with leaving it — matches this codebase's established precedent of
    # never dropping enum types on downgrade).
