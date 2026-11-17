"""tenants.slug — URL-safe identifier for the tenant-scoped segment of every
in-app route (https://.../<slug>/dashboard, .../<slug>/packs/123, ...).
Backfilled from each existing tenant's `name`, de-duplicated with a -2, -3,
... suffix on collision. Distinct from `name` so a display-name rename never
breaks a bookmarked link.

Revision ID: 0045_tenant_slug
Revises: 0044_template_due_anchor
Create Date: 2026-09-11
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0045_tenant_slug"
down_revision: Union[str, None] = "0044_template_due_anchor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return (slug[:63].strip("-")) or "org"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("tenants", sa.Column("slug", sa.String(length=80), nullable=True))

    rows = bind.execute(sa.text("SELECT id, name FROM tenants ORDER BY created_at")).fetchall()
    seen: set[str] = set()
    for tenant_id, name in rows:
        base = _slugify(name or "org")
        candidate = base
        suffix = 2
        while candidate in seen:
            candidate = f"{base}-{suffix}"
            suffix += 1
        seen.add(candidate)
        bind.execute(
            sa.text("UPDATE tenants SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": tenant_id},
        )

    op.alter_column("tenants", "slug", nullable=False)
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_column("tenants", "slug")
