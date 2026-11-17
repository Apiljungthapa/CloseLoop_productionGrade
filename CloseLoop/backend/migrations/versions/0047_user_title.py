"""users.title — free-text job title/designation (e.g. "Finance Controller",
"Ledger Accountant") shown alongside a user's name. Purely cosmetic: it
never participates in any is_admin/is_team_lead/is_team_member permission
check, which still key off `role` alone. Lets a tenant label people more
specifically than the 4 coarse permission tiers without touching access
control.

Revision ID: 0047_user_title
Revises: 0046_password_reset_tokens
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0047_user_title"
down_revision: Union[str, None] = "0046_password_reset_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("title", sa.String(length=150), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "title")
