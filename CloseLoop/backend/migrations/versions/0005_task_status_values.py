"""task status enum values

Revision ID: 0005_task_status_values
Revises: 0004_entity_group_is_active
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_task_status_values"
down_revision: Union[str, None] = "0004_entity_group_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None 


def upgrade() -> None:
    op.execute(
        "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'ASSIGNED';"
    )
    op.execute(
        "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'PENDING_REVIEW';"
    )
    op.execute(
        "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'REJECTED';"
    )
    op.execute(
        "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'CARRIED_FORWARD';"
    )


def downgrade() -> None:
    pass
