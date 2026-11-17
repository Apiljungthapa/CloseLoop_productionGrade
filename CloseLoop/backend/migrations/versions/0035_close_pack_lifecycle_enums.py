"""Enum groundwork for the multi-period Close Pack lifecycle.

  * task_status: add UPCOMING (a task whose pack period window hasn't
    reached its active_from yet — visible but locked).
  * new type close_pack_lifecycle (DRAFT | ACTIVE | COMPLETED).

Split from 0036 because a value added to an enum via ALTER TYPE ... ADD
VALUE must be committed before it can be referenced.

Revision ID: 0035_close_pack_lifecycle_enums
Revises: 0034_close_pack_calendar_and_frequency
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_close_pack_lifecycle_enums"
down_revision: Union[str, None] = "0034_close_pack_calendar_and_frequency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'UPCOMING'")
    postgresql.ENUM(
        "DRAFT", "ACTIVE", "COMPLETED", name="close_pack_lifecycle"
    ).create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # PostgreSQL can't remove an enum value in place; leave UPCOMING.
    postgresql.ENUM(
        "DRAFT", "ACTIVE", "COMPLETED", name="close_pack_lifecycle"
    ).drop(op.get_bind(), checkfirst=True)
