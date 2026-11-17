"""Required/Optional for recurring tasks — task_templates / tasks get
`is_required` (boolean, default true): a DAILY-frequency template (and the
Task instances generated from it) can be marked Optional, meaning an
uncompleted instance never counts as overdue or against completion
compliance, while still being generated and visible for tracking. Every
other frequency, and every existing row, defaults to True (Required) —
the behavior every task had before this field existed.

Scoped to task_templates/tasks only (not sub_task_templates/sub_tasks) —
this first pass covers top-level DAILY recurring tasks.

Revision ID: 0053_task_required_optional
Revises: 0052_recurring_due_days
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0053_task_required_optional"
down_revision: Union[str, None] = "0052_recurring_due_days"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("task_templates", "tasks")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table_name: str, column_name: str) -> bool:
        return column_name in {c["name"] for c in inspector.get_columns(table_name)}

    for table in _TABLES:
        if not _has_column(table, "is_required"):
            op.add_column(
                table,
                sa.Column(
                    "is_required",
                    sa.Boolean(),
                    nullable=False,
                    server_default="true",
                ),
            )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "is_required")
