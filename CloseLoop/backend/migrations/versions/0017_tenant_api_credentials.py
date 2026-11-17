"""Add per-tenant OAuth2 client-credentials pair (client_id / client_secret_hash).

Revision ID: 0017_tenant_api_credentials
Revises: 0016_add_daily_close_pack_type
Create Date: 2026-08-26

Machine-to-machine API access (integrations/scripts) for a tenant, entirely
separate from human login which stays email+password. Only the secret's
hash is persisted — the raw secret is shown once, at creation/rotation
time, same pattern as RefreshToken.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_tenant_api_credentials"
down_revision: Union[str, None] = "0016_add_daily_close_pack_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants", sa.Column("client_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "tenants",
        sa.Column("client_secret_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "client_secret_rotated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_unique_constraint("uq_tenants_client_id", "tenants", ["client_id"])
    op.create_index("ix_tenants_client_id", "tenants", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_tenants_client_id", table_name="tenants")
    op.drop_constraint("uq_tenants_client_id", "tenants", type_="unique")
    op.drop_column("tenants", "client_secret_rotated_at")
    op.drop_column("tenants", "client_secret_hash")
    op.drop_column("tenants", "client_id")
