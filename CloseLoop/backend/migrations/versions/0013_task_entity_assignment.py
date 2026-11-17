"""whole-entity task assignment: assigned_entity_wide flag + task_entity_assignees table

Revision ID: 0013_task_entity_assignment
Revises: 0012_task_member_assignment
Create Date: 2026-07-02

Lets a Team Lead assign a LEAF task (no sub-tasks) to every Team Member
scoped to the task's entity instead of a single member. Each member gets a
TaskEntityAssignee row; the task only auto-submits for the Lead's review
once every row is marked done (see routers/tasks.py assign-entity /
mark-done-entity). Mutually exclusive with member_assigned_to_id.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0013_task_entity_assignment"
down_revision: Union[str, None] = "0012_task_member_assignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "assigned_entity_wide",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "task_entity_assignees",
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
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_entity_assignee_user"),
    )
    op.create_index(
        "ix_task_entity_assignees_tenant", "task_entity_assignees", ["tenant_id"]
    )
    op.create_index(
        "ix_task_entity_assignees_task", "task_entity_assignees", ["task_id"]
    )
    op.create_index(
        "ix_task_entity_assignees_user", "task_entity_assignees", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_entity_assignees_user", table_name="task_entity_assignees")
    op.drop_index("ix_task_entity_assignees_task", table_name="task_entity_assignees")
    op.drop_index("ix_task_entity_assignees_tenant", table_name="task_entity_assignees")
    op.drop_table("task_entity_assignees")
    op.drop_column("tasks", "assigned_entity_wide")
