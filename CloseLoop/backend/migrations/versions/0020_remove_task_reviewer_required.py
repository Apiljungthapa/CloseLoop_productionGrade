"""Drop tasks.reviewer_required — unused, never actually gated review logic.

Revision ID: 0020_remove_task_reviewer_required
Revises: 0019_remove_checkpoint_subsystem
Create Date: 2026-08-31

Only the `tasks` table is affected. `task_templates.reviewer_required`
is untouched — templates still carry it for the close-pack editor; it's
just no longer copied onto generated Task rows.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_remove_task_reviewer_required"
down_revision: Union[str, None] = "0019_remove_checkpoint_subsystem"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("tasks", "reviewer_required")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "reviewer_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
