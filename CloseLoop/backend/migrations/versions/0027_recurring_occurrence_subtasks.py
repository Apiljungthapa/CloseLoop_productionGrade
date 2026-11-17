"""Add subtasks on recurring-task occurrences — same leaf submit/review
lifecycle as SubTask, reusing the recurring_occurrence_status enum.

Revision ID: 0027_recurring_occurrence_subtasks
Revises: 0026_recurring_tasks
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0027_recurring_occurrence_subtasks"
down_revision: Union[str, None] = "0026_recurring_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reuse the existing enum type created by 0026 — do NOT re-create it here.
# NOTE: create_type=False on a plain sa.Enum is NOT reliably honored by
# op.create_table (SQLAlchemy still emits CREATE TYPE, causing
# DuplicateObjectError — confirmed against a real Postgres run). The
# dialect-specific postgresql.ENUM DOES honor it correctly.
_occurrence_status_enum = postgresql.ENUM(
    "ASSIGNED", "PENDING_REVIEW", "COMPLETED", "REJECTED", "CANCELLED",
    name="recurring_occurrence_status",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "recurring_occurrence_subtasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "occurrence_id", sa.String(length=64),
            sa.ForeignKey("recurring_task_occurrences.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "assigned_to_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_recurring_occ_subtasks_tenant", "recurring_occurrence_subtasks", ["tenant_id"]
    )
    op.create_index(
        "ix_recurring_occ_subtasks_occurrence", "recurring_occurrence_subtasks", ["occurrence_id"]
    )
    op.create_index(
        "ix_recurring_occ_subtasks_assigned", "recurring_occurrence_subtasks", ["assigned_to_id"]
    )


def downgrade() -> None:
    op.drop_table("recurring_occurrence_subtasks")
