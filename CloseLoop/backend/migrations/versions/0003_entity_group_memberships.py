"""entity group memberships for multi-group entities

Revision ID: 0003_entity_group_memberships
Revises: 0002_checkpoint_assignees
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003_entity_group_memberships"
down_revision: Union[str, None] = "0002_checkpoint_assignees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_group_memberships",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(length=64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(length=64),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("entity_id", "group_id", name="uq_entity_group_membership"),
    )
    op.create_index(
        "ix_entity_group_memberships_tenant",
        "entity_group_memberships",
        ["tenant_id"],
    )
    op.create_index(
        "ix_entity_group_memberships_entity",
        "entity_group_memberships",
        ["entity_id"],
    )
    op.create_index(
        "ix_entity_group_memberships_group",
        "entity_group_memberships",
        ["group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_group_memberships_group", table_name="entity_group_memberships")
    op.drop_index("ix_entity_group_memberships_entity", table_name="entity_group_memberships")
    op.drop_index("ix_entity_group_memberships_tenant", table_name="entity_group_memberships")
    op.drop_table("entity_group_memberships")
