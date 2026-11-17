"""Sub-task templates recur independently; drop tags/priority off them.

Revision ID: 0049_subtask_template_frequency
Revises: 0048_template_tags_priority
Create Date: 2026-09-14

A sub-task template can now carry its own frequency/interval_count/
days_of_week/recurrence_start/recurrence_end (mirroring task_templates,
see models.SubTaskTemplate) — one sub-task row generates per occurrence
UNDER EACH parent task occurrence. tags/priority (added on both tables
in 0048) turned out to only be wanted on task_templates, not
sub_task_templates — dropped here.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0049_subtask_template_frequency"
down_revision: Union[str, None] = "0048_template_tags_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FREQ = postgresql.ENUM(name="recurrence_frequency", create_type=False)


def upgrade() -> None:
    op.drop_column("sub_task_templates", "priority")
    op.drop_column("sub_task_templates", "tags")
    op.add_column("sub_task_templates", sa.Column("frequency", _FREQ, nullable=True))
    op.add_column(
        "sub_task_templates",
        sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "sub_task_templates",
        sa.Column("days_of_week", postgresql.ARRAY(sa.Integer()), nullable=True),
    )
    op.add_column("sub_task_templates", sa.Column("recurrence_start", sa.Date(), nullable=True))
    op.add_column("sub_task_templates", sa.Column("recurrence_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("sub_task_templates", "recurrence_end")
    op.drop_column("sub_task_templates", "recurrence_start")
    op.drop_column("sub_task_templates", "days_of_week")
    op.drop_column("sub_task_templates", "interval_count")
    op.drop_column("sub_task_templates", "frequency")
    op.add_column(
        "sub_task_templates",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=50)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "sub_task_templates",
        sa.Column(
            "priority",
            sa.Enum("LOW", "MEDIUM", "HIGH", name="priority"),
            nullable=False,
            server_default="MEDIUM",
        ),
    )
