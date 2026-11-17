"""add checkpoint sub-tasks table

Revision ID: 0009_checkpoint_sub_tasks
Revises: 0008_alembic_version_varchar100
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

# Reuse the existing task_status type rather than recreating it.
task_status = PGEnum(
    "NOT_STARTED",
    "ASSIGNED",
    "IN_PROGRESS",
    "PENDING_REVIEW",
    "COMPLETED",
    "REJECTED",
    "CARRIED_FORWARD",
    name="task_status",
    create_type=False,
)

# revision identifiers, used by Alembic.
revision: str = "0009_checkpoint_sub_tasks"
down_revision: Union[str, None] = "0008_alembic_version_varchar100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checkpoint_sub_tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("source_template_id", sa.String(length=64), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("assigned_to_id", sa.String(length=64), nullable=True),
        sa.Column("reviewer_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", task_status, nullable=False, server_default="NOT_STARTED"),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_checkpoint_subtasks_tenant", "checkpoint_sub_tasks", ["tenant_id"])
    op.create_index("ix_checkpoint_subtasks_checkpoint", "checkpoint_sub_tasks", ["checkpoint_id"])
    op.create_index("ix_checkpoint_subtasks_assigned", "checkpoint_sub_tasks", ["assigned_to_id"])
    op.create_index("ix_checkpoint_subtasks_reviewer", "checkpoint_sub_tasks", ["reviewer_id"])
    op.create_index("ix_checkpoint_subtasks_tenant_status", "checkpoint_sub_tasks", ["tenant_id","status"])


def downgrade() -> None:
    op.drop_index("ix_checkpoint_subtasks_tenant", table_name="checkpoint_sub_tasks")
    op.drop_index("ix_checkpoint_subtasks_checkpoint", table_name="checkpoint_sub_tasks")
    op.drop_index("ix_checkpoint_subtasks_assigned", table_name="checkpoint_sub_tasks")
    op.drop_index("ix_checkpoint_subtasks_reviewer", table_name="checkpoint_sub_tasks")
    op.drop_index("ix_checkpoint_subtasks_tenant_status", table_name="checkpoint_sub_tasks")
    op.drop_table("checkpoint_sub_tasks")
