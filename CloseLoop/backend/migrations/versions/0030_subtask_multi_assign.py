"""Whole-entity / hand-picked multi-assignee support for regular sub-tasks
— mirrors Task/TaskEntityAssignee one level down (see also 0013 and
0028 which did the same for tasks and recurring occurrences).

Revision ID: 0030_subtask_multi_assign
Revises: 0029_recurring_rule_multi_assign
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_subtask_multi_assign"
down_revision: Union[str, None] = "0029_recurring_rule_multi_assign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sub_tasks",
        sa.Column(
            "assigned_entity_wide",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "sub_task_entity_assignees",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subtask_id",
            sa.String(length=64),
            sa.ForeignKey("sub_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_notes", sa.Text(), nullable=True),
        sa.Column("completion_time_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "subtask_id", "user_id", name="uq_sub_task_entity_assignee_user"
        ),
    )
    op.create_index(
        "ix_sub_task_entity_assignees_tenant", "sub_task_entity_assignees", ["tenant_id"]
    )
    op.create_index(
        "ix_sub_task_entity_assignees_subtask", "sub_task_entity_assignees", ["subtask_id"]
    )
    op.create_index(
        "ix_sub_task_entity_assignees_user", "sub_task_entity_assignees", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sub_task_entity_assignees_user", table_name="sub_task_entity_assignees"
    )
    op.drop_index(
        "ix_sub_task_entity_assignees_subtask", table_name="sub_task_entity_assignees"
    )
    op.drop_index(
        "ix_sub_task_entity_assignees_tenant", table_name="sub_task_entity_assignees"
    )
    op.drop_table("sub_task_entity_assignees")
    op.drop_column("sub_tasks", "assigned_entity_wide")
