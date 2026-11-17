"""Add completion metadata fields to tasks, subtasks, and checkpoints.

Revision ID: 0015_completion_submission_metadata
Revises: 0014_auth_sessions
Create Date: 2026-07-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_completion_submission_metadata"
down_revision: Union[str, None] = "0014_auth_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table_name: str, column_name: str) -> bool:
        return column_name in {c["name"] for c in inspector.get_columns(table_name)}

    if not _has_column("tasks", "completion_notes"):
        op.add_column("tasks", sa.Column("completion_notes", sa.Text(), nullable=True))
    if not _has_column("tasks", "completion_time_minutes"):
        op.add_column("tasks", sa.Column("completion_time_minutes", sa.Integer(), nullable=True))
    if not _has_column("tasks", "submitted_by_id"):
        op.add_column("tasks", sa.Column("submitted_by_id", sa.String(length=64), nullable=True))
    if not _has_column("tasks", "submitted_at"):
        op.add_column("tasks", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))

    if not _has_column("sub_tasks", "completion_notes"):
        op.add_column("sub_tasks", sa.Column("completion_notes", sa.Text(), nullable=True))
    if not _has_column("sub_tasks", "completion_time_minutes"):
        op.add_column("sub_tasks", sa.Column("completion_time_minutes", sa.Integer(), nullable=True))
    if not _has_column("sub_tasks", "submitted_by_id"):
        op.add_column("sub_tasks", sa.Column("submitted_by_id", sa.String(length=64), nullable=True))
    if not _has_column("sub_tasks", "submitted_at"):
        op.add_column("sub_tasks", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))

    if not _has_column("checkpoints", "submitted_by_id"):
        op.add_column("checkpoints", sa.Column("submitted_by_id", sa.String(length=64), nullable=True))
    if not _has_column("checkpoints", "submitted_at"):
        op.add_column("checkpoints", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("checkpoints", "completion_notes"):
        op.add_column("checkpoints", sa.Column("completion_notes", sa.Text(), nullable=True))
    if not _has_column("checkpoints", "completion_time_minutes"):
        op.add_column("checkpoints", sa.Column("completion_time_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("checkpoints", "completion_time_minutes")
    op.drop_column("checkpoints", "completion_notes")
    op.drop_column("checkpoints", "submitted_at")
    op.drop_column("checkpoints", "submitted_by_id")
    op.drop_column("sub_tasks", "submitted_at")
    op.drop_column("sub_tasks", "submitted_by_id")
    op.drop_column("sub_tasks", "completion_time_minutes")
    op.drop_column("sub_tasks", "completion_notes")
    op.drop_column("tasks", "submitted_at")
    op.drop_column("tasks", "submitted_by_id")
    op.drop_column("tasks", "completion_time_minutes")
    op.drop_column("tasks", "completion_notes")
