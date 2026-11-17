"""task_templates.due_anchor / sub_task_templates.due_anchor — whether a
template's `due_day_of_period` counts forward from the period START (day 1
= first day) or backward from the period END (day 1 = last day, "N days
from close"). Existing rows keep the historical PERIOD_START behaviour.

Revision ID: 0044_template_due_anchor
Revises: 0043_task_recurrence_rule
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0044_template_due_anchor"
down_revision: Union[str, None] = "0043_task_recurrence_rule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALUES = ("PERIOD_START", "PERIOD_END")


def upgrade() -> None:
    due_anchor = postgresql.ENUM(*_VALUES, name="due_anchor")
    due_anchor.create(op.get_bind(), checkfirst=True)
    for table in ("task_templates", "sub_task_templates"):
        op.add_column(
            table,
            sa.Column(
                "due_anchor",
                postgresql.ENUM(*_VALUES, name="due_anchor", create_type=False),
                nullable=False,
                server_default="PERIOD_START",
            ),
        )


def downgrade() -> None:
    for table in ("task_templates", "sub_task_templates"):
        op.drop_column(table, "due_anchor")
    op.execute("DROP TYPE IF EXISTS due_anchor")
