"""Add per-assignee completion metadata to task_entity_assignees.

Multi-assignee tasks (whole-entity or a hand-picked multi-assign) now
require each person's own Time Taken / Notes when they mark their row
done, same as the existing single-assignee Task/SubTask fields.

Revision ID: 0024_entity_assignee_completion_metadata
Revises: 0023_add_tags_attachments
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_entity_assignee_completion_metadata"
down_revision: Union[str, None] = "0023_add_tags_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table_name: str, column_name: str) -> bool:
        return column_name in {c["name"] for c in inspector.get_columns(table_name)}

    if not _has_column("task_entity_assignees", "completion_notes"):
        op.add_column(
            "task_entity_assignees", sa.Column("completion_notes", sa.Text(), nullable=True)
        )
    if not _has_column("task_entity_assignees", "completion_time_minutes"):
        op.add_column(
            "task_entity_assignees",
            sa.Column("completion_time_minutes", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("task_entity_assignees", "completion_time_minutes")
    op.drop_column("task_entity_assignees", "completion_notes")
