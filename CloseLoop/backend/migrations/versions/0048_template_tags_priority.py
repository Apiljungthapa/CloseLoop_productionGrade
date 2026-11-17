"""Add tags + priority to task_templates and sub_task_templates.

Revision ID: 0048_template_tags_priority
Revises: 0047_user_title
Create Date: 2026-09-14

Mirrors Task/SubTask.tags + .priority (0021, 0023) one level up, so a
template can set a default tag list / priority that carries onto every
Task/SubTask generated from it.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0048_template_tags_priority"
down_revision: Union[str, None] = "0047_user_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_priority_enum = sa.Enum("LOW", "MEDIUM", "HIGH", name="priority")


def upgrade() -> None:
    _priority_enum.create(op.get_bind(), checkfirst=True)
    for table in ("task_templates", "sub_task_templates"):
        op.add_column(
            table,
            sa.Column(
                "tags",
                postgresql.ARRAY(sa.String(length=50)),
                nullable=False,
                server_default="{}",
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "priority",
                _priority_enum,
                nullable=False,
                server_default="MEDIUM",
            ),
        )


def downgrade() -> None:
    for table in ("sub_task_templates", "task_templates"):
        op.drop_column(table, "priority")
        op.drop_column(table, "tags")
