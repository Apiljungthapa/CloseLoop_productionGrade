"""add due_date to checkpoints

Revision ID: 0011_checkpoint_due_date
Revises: 0010_checkpoint_approval_workflow
Create Date: 2026-06-26

A Team Lead can set a localized due date on a Checkpoint within the
deployed plan instance. The Checkpoint due date must fall inside the
plan's period and on or before its parent Sub-task's due date.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0011_checkpoint_due_date"
down_revision: Union[str, None] = "0010_checkpoint_approval_workflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checkpoints",
        sa.Column("due_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("checkpoints", "due_date")
