"""Add recurring task rules + occurrences — daily/weekly/biweekly/monthly/
custom schedule-driven tasks, independent of Close Packs and Periods.

Revision ID: 0026_recurring_tasks
Revises: 0025_period_cadence
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026_recurring_tasks"
down_revision: Union[str, None] = "0025_period_cadence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_frequency_enum = sa.Enum(
    "DAILY", "WEEKLY", "MONTHLY", "CUSTOM", name="recurrence_frequency"
)
_rule_status_enum = sa.Enum(
    "ACTIVE", "PAUSED", "ENDED", name="recurring_rule_status"
)
_occurrence_status_enum = sa.Enum(
    "ASSIGNED", "PENDING_REVIEW", "COMPLETED", "REJECTED", "CANCELLED",
    name="recurring_occurrence_status",
)


def upgrade() -> None:
    # Each enum type below is used by exactly one column in one table
    # created in this same migration, so it's created implicitly as part
    # of that op.create_table() call (SQLAlchemy emits CREATE TYPE for any
    # Enum column type it hasn't seen yet) — do NOT also .create() it
    # explicitly here, or the second attempt raises DuplicateObjectError
    # since create_table's own type-creation isn't checkfirst-guarded.
    op.create_table(
        "recurring_task_rules",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "entity_id", sa.String(length=64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "created_by_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "assigned_to_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "member_assigned_to_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("frequency", _frequency_enum, nullable=False),
        sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("days_of_week", sa.ARRAY(sa.Integer()), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("business_days_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "period_id", sa.String(length=64),
            sa.ForeignKey("periods.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("due_time", sa.Time(), nullable=True),
        sa.Column("grace_period_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("status", _rule_status_enum, nullable=False, server_default="ACTIVE"),
        sa.Column("last_generated_through", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(),
        ),
        sa.CheckConstraint("interval_count >= 1", name="ck_recurring_rule_interval_positive"),
    )
    op.create_index("ix_recurring_rules_tenant", "recurring_task_rules", ["tenant_id"])
    op.create_index("ix_recurring_rules_entity", "recurring_task_rules", ["entity_id"])
    op.create_index(
        "ix_recurring_rules_tenant_status", "recurring_task_rules", ["tenant_id", "status"]
    )
    op.create_index("ix_recurring_rules_assigned", "recurring_task_rules", ["assigned_to_id"])
    op.create_index(
        "ix_recurring_rules_member_assigned", "recurring_task_rules", ["member_assigned_to_id"]
    )

    op.create_table(
        "recurring_task_occurrences",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "rule_id", sa.String(length=64),
            sa.ForeignKey("recurring_task_rules.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "entity_id", sa.String(length=64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "assigned_to_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "member_assigned_to_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "status", _occurrence_status_enum, nullable=False, server_default="ASSIGNED"
        ),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column("completion_notes", sa.Text(), nullable=True),
        sa.Column("completion_time_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "submitted_by_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint("rule_id", "occurrence_date", name="uq_recurring_occurrence_date"),
    )
    op.create_index("ix_recurring_occ_tenant", "recurring_task_occurrences", ["tenant_id"])
    op.create_index("ix_recurring_occ_rule", "recurring_task_occurrences", ["rule_id"])
    op.create_index("ix_recurring_occ_entity", "recurring_task_occurrences", ["entity_id"])
    op.create_index("ix_recurring_occ_assigned", "recurring_task_occurrences", ["assigned_to_id"])
    op.create_index(
        "ix_recurring_occ_member_assigned", "recurring_task_occurrences", ["member_assigned_to_id"]
    )
    op.create_index(
        "ix_recurring_occ_tenant_status", "recurring_task_occurrences", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_recurring_occ_tenant_due", "recurring_task_occurrences", ["tenant_id", "due_date"]
    )


def downgrade() -> None:
    op.drop_table("recurring_task_occurrences")
    op.drop_table("recurring_task_rules")
    bind = op.get_bind()
    _occurrence_status_enum.drop(bind, checkfirst=True)
    _rule_status_enum.drop(bind, checkfirst=True)
    _frequency_enum.drop(bind, checkfirst=True)
