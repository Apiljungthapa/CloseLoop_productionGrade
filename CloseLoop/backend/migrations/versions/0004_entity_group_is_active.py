"""entity group is_active flag + search indexes

Revision ID: 0004_entity_group_is_active
Revises: 0003_entity_group_memberships
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004_entity_group_is_active"
down_revision: Union[str, None] = "0003_entity_group_memberships"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_active column (default True so existing rows stay active).
    op.add_column(
        "entity_groups",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # Useful supporting indexes
    op.create_index(
        "ix_entity_groups_tenant_active",
        "entity_groups",
        ["tenant_id", "is_active"],
    )
    op.create_index(
        "ix_entity_groups_tenant_name",
        "entity_groups",
        ["tenant_id", "name"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_groups_tenant_name", table_name="entity_groups")
    op.drop_index("ix_entity_groups_tenant_active", table_name="entity_groups")
    op.drop_column("entity_groups", "is_active")
