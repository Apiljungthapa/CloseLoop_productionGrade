"""increase alembic version_num length to 100

Adjusts the `alembic_version.version_num` column to support longer
revision identifiers used in some workflows.

Revision ID: 0008_alembic_version_varchar100
Revises: 0007_time_entries_and_report_snapshots
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0008_alembic_version_varchar100"
down_revision: Union[str, None] = "0007_time_entries_and_report_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Increase alembic version length to 100 characters.
    # Use a plain ALTER TABLE; Postgres will automatically cast compatible
    # types. If the DB requires a USING clause it should still work for
    # string types.
    op.execute(
        """
        ALTER TABLE alembic_version
        ALTER COLUMN version_num TYPE VARCHAR(100);
        """
    )


def downgrade() -> None:
    # Revert to the historical default length of 32 characters.
    op.execute(
        """
        ALTER TABLE alembic_version 
        ALTER COLUMN version_num TYPE VARCHAR(32);
        """
    )
