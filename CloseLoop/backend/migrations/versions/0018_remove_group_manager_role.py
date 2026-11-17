"""Remove GROUP_MANAGER from the user_role enum.

Revision ID: 0018_remove_group_manager_role
Revises: 0017_tenant_api_credentials
Create Date: 2026-08-31

Role model simplifies to PLATFORM_OWNER / SUPER_ADMIN / TEAM_LEAD /
TEAM_MEMBER only. Any existing GROUP_MANAGER user is converted to
SUPER_ADMIN first (they already had tenant-wide entity oversight, so this
keeps their access level rather than narrowing it) — then the enum value
itself is removed. Postgres can't drop an enum value in place, so the
type is rebuilt: rename old -> create new without GROUP_MANAGER -> cast
the column across -> drop the old type.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_remove_group_manager_role"
down_revision: Union[str, None] = "0017_tenant_api_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_ROLES = ("PLATFORM_OWNER", "SUPER_ADMIN", "TEAM_LEAD", "TEAM_MEMBER")
_OLD_ROLES = ("PLATFORM_OWNER", "SUPER_ADMIN", "GROUP_MANAGER", "TEAM_LEAD", "TEAM_MEMBER")


def upgrade() -> None:
    # 1. Migrate any existing GROUP_MANAGER users to SUPER_ADMIN before the
    #    enum value they reference is removed.
    op.execute("UPDATE users SET role = 'SUPER_ADMIN' WHERE role = 'GROUP_MANAGER'")

    # 2. Rebuild the enum type without GROUP_MANAGER.
    op.execute("ALTER TYPE user_role RENAME TO user_role_old")
    new_enum = sa.Enum(*_NEW_ROLES, name="user_role")
    new_enum.create(op.get_bind())
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role "
        "USING role::text::user_role"
    )
    op.execute("DROP TYPE user_role_old")


def downgrade() -> None:
    # Re-add GROUP_MANAGER as a valid value. Does NOT restore any user to
    # that role — the SUPER_ADMIN conversion from upgrade() is one-way.
    op.execute("ALTER TYPE user_role RENAME TO user_role_new")
    old_enum = sa.Enum(*_OLD_ROLES, name="user_role")
    old_enum.create(op.get_bind())
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role "
        "USING role::text::user_role"
    )
    op.execute("DROP TYPE user_role_new")
