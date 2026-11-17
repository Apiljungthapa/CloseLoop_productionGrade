"""Add cadence to period_configurations — universal (not just fixed-week
"picnic"-style) period generation: FIXED_WEEKS, CALENDAR_MONTH, or
CALENDAR_QUARTER.

Revision ID: 0025_period_cadence
Revises: 0024_entity_assignee_completion_metadata
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025_period_cadence"
down_revision: Union[str, None] = "0024_entity_assignee_completion_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERIOD_CADENCE_VALUES = ("FIXED_WEEKS", "CALENDAR_MONTH", "CALENDAR_QUARTER")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table_name: str, column_name: str) -> bool:
        return column_name in {c["name"] for c in inspector.get_columns(table_name)}

    period_cadence = sa.Enum(*PERIOD_CADENCE_VALUES, name="period_cadence")
    period_cadence.create(bind, checkfirst=True)

    if not _has_column("period_configurations", "cadence"):
        op.add_column(
            "period_configurations",
            sa.Column(
                "cadence",
                period_cadence,
                nullable=False,
                server_default="FIXED_WEEKS",
            ),
        )
        # Drop the server default after backfilling existing rows — new
        # rows should rely on the ORM-level default, matching every other
        # enum column in this schema.
        op.alter_column("period_configurations", "cadence", server_default=None)


def downgrade() -> None:
    op.drop_column("period_configurations", "cadence")
    sa.Enum(name="period_cadence").drop(op.get_bind(), checkfirst=True)
