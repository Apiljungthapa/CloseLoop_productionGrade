"""Whole-entity / hand-picked multi-assignee support for recurring
occurrences AND their subtasks — mirrors Task/TaskEntityAssignee at both
levels.

Revision ID: 0028_recurring_multi_assign
Revises: 0027_recurring_occurrence_subtasks
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_recurring_multi_assign"
down_revision: Union[str, None] = "0027_recurring_occurrence_subtasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_task_occurrences",
        sa.Column("assigned_entity_wide", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "recurring_occurrence_subtasks",
        sa.Column("assigned_entity_wide", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "recurring_occurrence_assignees",
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
            "user_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_notes", sa.Text(), nullable=True),
        sa.Column("completion_time_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("occurrence_id", "user_id", name="uq_recurring_occ_assignee_user"),
    )
    op.create_index(
        "ix_recurring_occ_assignees_tenant", "recurring_occurrence_assignees", ["tenant_id"]
    )
    op.create_index(
        "ix_recurring_occ_assignees_occurrence", "recurring_occurrence_assignees", ["occurrence_id"]
    )
    op.create_index(
        "ix_recurring_occ_assignees_user", "recurring_occurrence_assignees", ["user_id"]
    )

    op.create_table(
        "recurring_occurrence_subtask_assignees",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "subtask_id", sa.String(length=64),
            sa.ForeignKey("recurring_occurrence_subtasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_notes", sa.Text(), nullable=True),
        sa.Column("completion_time_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("subtask_id", "user_id", name="uq_recurring_subtask_assignee_user"),
    )
    op.create_index(
        "ix_recurring_subtask_assignees_tenant", "recurring_occurrence_subtask_assignees", ["tenant_id"]
    )
    op.create_index(
        "ix_recurring_subtask_assignees_subtask", "recurring_occurrence_subtask_assignees", ["subtask_id"]
    )
    op.create_index(
        "ix_recurring_subtask_assignees_user", "recurring_occurrence_subtask_assignees", ["user_id"]
    )


def downgrade() -> None:
    op.drop_table("recurring_occurrence_subtask_assignees")
    op.drop_table("recurring_occurrence_assignees")
    op.drop_column("recurring_occurrence_subtasks", "assigned_entity_wide")
    op.drop_column("recurring_task_occurrences", "assigned_entity_wide")
