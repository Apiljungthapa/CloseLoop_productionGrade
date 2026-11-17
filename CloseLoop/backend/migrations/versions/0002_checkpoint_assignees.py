"""checkpoint_assignees table for multi-assignee + per-contributor counters

Revision ID: 0002_checkpoint_assignees
Revises: 0001_initial
Create Date: 2026-06-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002_checkpoint_assignees"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checkpoint_assignees",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_id",
            sa.String(length=64),
            sa.ForeignKey("checkpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "count_contributed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_contribution_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "checkpoint_id", "user_id", name="uq_checkpoint_assignee_user"
        ),
    )
    op.create_index(
        "ix_checkpoint_assignees_tenant", "checkpoint_assignees", ["tenant_id"]
    )
    op.create_index(
        "ix_checkpoint_assignees_checkpoint",
        "checkpoint_assignees",
        ["checkpoint_id"],
    )
    op.create_index("ix_checkpoint_assignees_user", "checkpoint_assignees", ["user_id"])

    # Backfill: for every existing checkpoint, seed one assignee = the subtask's owner.
    # Their count_contributed mirrors the checkpoint's current count_value so totals
    # remain consistent.
    op.execute(
        """
        INSERT INTO checkpoint_assignees
            (id, tenant_id, checkpoint_id, user_id, count_contributed, created_at)
        SELECT
            md5(random()::text || clock_timestamp()::text)::varchar AS id,
            c.tenant_id,
            c.id AS checkpoint_id,
            s.assigned_to_id AS user_id,
            COALESCE(c.count_value, 0) AS count_contributed,
            now() AS created_at
        FROM checkpoints c
        JOIN sub_tasks s ON s.id = c.sub_task_id
        WHERE s.assigned_to_id IS NOT NULL
        ON CONFLICT (checkpoint_id, user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_checkpoint_assignees_user", table_name="checkpoint_assignees")
    op.drop_index(
        "ix_checkpoint_assignees_checkpoint", table_name="checkpoint_assignees"
    )
    op.drop_index("ix_checkpoint_assignees_tenant", table_name="checkpoint_assignees")
    op.drop_table("checkpoint_assignees")
