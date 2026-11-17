"""Add comments table — task detail side-panel discussion thread.

Revision ID: 0022_add_comments
Revises: 0021_add_task_subtask_priority
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_add_comments"
down_revision: Union[str, None] = "0021_add_task_subtask_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comments",
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
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_comments_tenant", "comments", ["tenant_id"])
    op.create_index("ix_comments_task", "comments", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_comments_task", table_name="comments")
    op.drop_index("ix_comments_tenant", table_name="comments")
    op.drop_table("comments")
