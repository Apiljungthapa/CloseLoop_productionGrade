"""Add tags (Task/SubTask) and an attachments table.

Revision ID: 0023_add_tags_attachments
Revises: 0022_add_comments
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0023_add_tags_attachments"
down_revision: Union[str, None] = "0022_add_comments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=50)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "sub_tasks",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=50)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_index("ix_tasks_tags", "tasks", ["tags"], postgresql_using="gin")
    op.create_index("ix_subtasks_tags", "sub_tasks", ["tags"], postgresql_using="gin")

    op.create_table(
        "attachments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "subtask_id",
            sa.String(length=64),
            sa.ForeignKey("sub_tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_by_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=150), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(task_id IS NOT NULL) <> (subtask_id IS NOT NULL)",
            name="ck_attachments_one_parent",
        ),
    )
    op.create_index("ix_attachments_tenant", "attachments", ["tenant_id"])
    op.create_index("ix_attachments_task", "attachments", ["task_id"])
    op.create_index("ix_attachments_subtask", "attachments", ["subtask_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_subtask", table_name="attachments")
    op.drop_index("ix_attachments_task", table_name="attachments")
    op.drop_index("ix_attachments_tenant", table_name="attachments")
    op.drop_table("attachments")
    op.drop_index("ix_subtasks_tags", table_name="sub_tasks")
    op.drop_index("ix_tasks_tags", table_name="tasks")
    op.drop_column("sub_tasks", "tags")
    op.drop_column("tasks", "tags")
