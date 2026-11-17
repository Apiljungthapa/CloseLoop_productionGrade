"""Post-Period Due Days — task_templates / sub_task_templates / tasks /
sub_tasks each get `due_date_type` (WITHIN_PERIOD | AFTER_PERIOD_END) and
`post_period_due_days` (integer, >= 0). WITHIN_PERIOD + 0 is the historical
behaviour, so every existing row keeps working unchanged: a due date must
still fall on/before its period's end date. AFTER_PERIOD_END lets a task/
sub-task's due date run up to `post_period_due_days` days past its period's
end date WITHOUT the period's own start/end ever being extended — see
app/services/due_date_rules.py, the single place this window is computed
and validated everywhere (forms, generation, carry-forward, dashboards).

Revision ID: 0051_post_period_due_days
Revises: 0050_assignee_due_dates
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0051_post_period_due_days"
down_revision: Union[str, None] = "0050_assignee_due_dates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALUES = ("WITHIN_PERIOD", "AFTER_PERIOD_END")
_TABLES = ("task_templates", "sub_task_templates", "tasks", "sub_tasks")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table_name: str, column_name: str) -> bool:
        return column_name in {c["name"] for c in inspector.get_columns(table_name)}

    due_date_type = postgresql.ENUM(*_VALUES, name="due_date_type")
    due_date_type.create(bind, checkfirst=True)

    for table in _TABLES:
        if not _has_column(table, "due_date_type"):
            op.add_column(
                table,
                sa.Column(
                    "due_date_type",
                    postgresql.ENUM(*_VALUES, name="due_date_type", create_type=False),
                    nullable=False,
                    server_default="WITHIN_PERIOD",
                ),
            )
        if not _has_column(table, "post_period_due_days"):
            op.add_column(
                table,
                sa.Column(
                    "post_period_due_days",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "post_period_due_days")
        op.drop_column(table, "due_date_type")
    op.execute("DROP TYPE IF EXISTS due_date_type")
