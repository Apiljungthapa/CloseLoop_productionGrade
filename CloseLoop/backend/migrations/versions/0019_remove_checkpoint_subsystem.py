"""Remove the checkpoint subsystem entirely — tasks and sub-tasks only.

Revision ID: 0019_remove_checkpoint_subsystem
Revises: 0018_remove_group_manager_role
Create Date: 2026-08-31

Drops checkpoint_sub_tasks, attachments, checkpoint_assignees,
checkpoints, and checkpoint_templates, plus the now-unused
time_entries.checkpoint_id column. This is a genuine data-loss migration
— any existing checkpoint/attachment rows are gone after upgrade(). The
hierarchy going forward is exactly Task -> SubTask; a sub-task is always
a leaf, completed directly via submit-for-review + review.

downgrade() intentionally does NOT resurrect the dropped tables/data —
same convention as 0016's enum-removal downgrade in this project. If the
checkpoint subsystem is ever reintroduced, do it as a fresh forward
migration rather than trying to reverse this one.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0019_remove_checkpoint_subsystem"
down_revision: Union[str, None] = "0018_remove_group_manager_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the FK-bearing column first — it references checkpoints, which
    # would otherwise block that table's drop below.
    op.drop_index("ix_time_entries_checkpoint", table_name="time_entries")
    op.drop_column("time_entries", "checkpoint_id")

    # Children of `checkpoints` first, then `checkpoints` itself, then its
    # own parent `checkpoint_templates`.
    op.drop_table("checkpoint_sub_tasks")
    op.drop_table("attachments")
    op.drop_table("checkpoint_assignees")
    op.drop_table("checkpoints")
    op.drop_table("checkpoint_templates")


def downgrade() -> None:
    # Deliberately not reversible — see module docstring.
    pass
