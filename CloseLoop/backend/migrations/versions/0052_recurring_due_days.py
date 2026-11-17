"""N-Day Due Date for recurring tasks/sub-tasks — task_templates /
sub_task_templates get `due_days` (integer, >= 0, default 0): for every
frequency EXCEPT "Once per Period" (Daily, Weekly, Monthly, Quarterly,
Semi-annual, Annual, and a Custom interval of any of those), each generated
occurrence's due date = that occurrence's own landing date + due_days,
recomputed fresh per occurrence — never the period's end date, never
waiting for the next occurrence.

tasks / sub_tasks get `occurrence_date` (date, nullable) — the raw calendar
date a RECURRING instance actually landed on, before the due_days offset;
NULL for a non-recurring ("Once per Period") task/sub-task and for a plain
one-off custom item, always set for a recurring instance going forward —
and `due_days` (integer, default 0, copied from the template/rule at
generation time purely for display). due_days=0 everywhere (and NULL
occurrence_date on every existing task/sub-task) preserves the historical
behaviour exactly — a recurring task's due date stays exactly its
occurrence date until an admin opts into a non-zero due_days.

Revision ID: 0052_recurring_due_days
Revises: 0051_post_period_due_days
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0052_recurring_due_days"
down_revision: Union[str, None] = "0051_post_period_due_days"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_TABLES = ("task_templates", "sub_task_templates")
_INSTANCE_TABLES = ("tasks", "sub_tasks")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table_name: str, column_name: str) -> bool:
        return column_name in {c["name"] for c in inspector.get_columns(table_name)}

    for table in _TEMPLATE_TABLES + _INSTANCE_TABLES:
        if not _has_column(table, "due_days"):
            op.add_column(
                table,
                sa.Column(
                    "due_days",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )

    for table in _INSTANCE_TABLES:
        if not _has_column(table, "occurrence_date"):
            op.add_column(table, sa.Column("occurrence_date", sa.Date(), nullable=True))


def downgrade() -> None:
    for table in _INSTANCE_TABLES:
        op.drop_column(table, "occurrence_date")
    for table in _TEMPLATE_TABLES + _INSTANCE_TABLES:
        op.drop_column(table, "due_days")
