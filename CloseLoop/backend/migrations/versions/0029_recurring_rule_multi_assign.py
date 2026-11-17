"""Rule-level multi-assignee configuration — whole-entity or a hand-picked
set applied to EVERY occurrence a rule generates going forward, rather
than converting each occurrence by hand.

Revision ID: 0029_recurring_rule_multi_assign
Revises: 0028_recurring_multi_assign
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0029_recurring_rule_multi_assign"
down_revision: Union[str, None] = "0028_recurring_multi_assign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_task_rules",
        sa.Column("assigned_entity_wide", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "recurring_task_rules",
        sa.Column("multi_assignee_ids", postgresql.ARRAY(sa.String(length=64)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recurring_task_rules", "multi_assignee_ids")
    op.drop_column("recurring_task_rules", "assigned_entity_wide")
