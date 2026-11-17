"""add per-checkpoint approval workflow columns

Revision ID: 0010_checkpoint_approval_workflow
Revises: 0009_checkpoint_sub_tasks
Create Date: 2026-06-26

Per the Team Lead Dashboard spec, approval is per-checkpoint (no separate
Reviewer role). Members complete checkpoints which move into a "submitted"
state; the Team Lead approves or rejects each one. When all checkpoints
are approved the parent sub-task auto-completes.

Adds four nullable / defaulted columns to `checkpoints`:

  * `submitted` (bool)         — member finished their work, awaiting lead
  * `submitted_by_id` (FK)     — the member who declared the work done
  * `submitted_at` (timestamp) — when submission happened
  * `rejection_note` (text)    — lead's rejection reason (cleared on resubmit)
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0010_checkpoint_approval_workflow"
down_revision: Union[str, None] = "0009_checkpoint_sub_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checkpoints",
        sa.Column(
            "submitted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "checkpoints",
        sa.Column("submitted_by_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "checkpoints",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "checkpoints",
        sa.Column("rejection_note", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_checkpoints_submitted_by_id_users",
        source_table="checkpoints",
        referent_table="users",
        local_cols=["submitted_by_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    # Backfill: any pre-existing checkpoint that was already `completed=true`
    # should be treated as both submitted and approved by the same user so
    # existing closed plans keep their semantics under the new model.
    op.execute(
        """
        UPDATE checkpoints
        SET submitted = TRUE,
            submitted_by_id = completed_by_id,
            submitted_at = COALESCE(completed_at, created_at)
        WHERE completed = TRUE
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_checkpoints_submitted_by_id_users",
        table_name="checkpoints",
        type_="foreignkey",
    )
    op.drop_column("checkpoints", "rejection_note")
    op.drop_column("checkpoints", "submitted_at")
    op.drop_column("checkpoints", "submitted_by_id")
    op.drop_column("checkpoints", "submitted")
