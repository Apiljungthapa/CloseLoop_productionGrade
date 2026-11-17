"""plan ownership + defensive custom-item flags

Adds the explicit Plan Ownership column (``plans.owner_id``) referenced by the
Hierarchical Plan Customization layer so an Admin can hand a generated plan
to a specific Team Lead. The Team Lead then becomes the authority for
structural edits / assignments on that plan, independent of their entity
scope (which is still honoured as a fallback).

Also defensively ensures that the per-row customization columns declared on
``tasks`` / ``sub_tasks`` / ``checkpoints`` (``is_custom``,
``source_template_id``) exist in the database. These columns are present in
the SQLAlchemy models and consumed by the existing routers, but were never
added by an explicit migration on top of ``0001_initial``. Using ``ADD
COLUMN IF NOT EXISTS`` keeps this idempotent for databases that already have
them (e.g. environments that previously relied on ``Base.metadata.create_all``).

This migration is strictly additive — it does NOT touch the period transition
mechanism (P7 → pre-seeded P8), the close-pack snapshot generation pipeline,
or the carry-forward queue rules.

Revision ID: 0006_plan_ownership_and_custom_flags
Revises: 0005_task_status_values
Create Date: 2026-06-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006_plan_ownership_and_custom_flags"
down_revision: Union[str, None] = "0005_task_status_values"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Plan ownership — the Team Lead an Admin hands the plan to.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE plans
        ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64)
        REFERENCES users(id) ON DELETE SET NULL
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_plans_owner ON plans(owner_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_plans_tenant_owner ON plans(tenant_id, owner_id)"
    )

    # ------------------------------------------------------------------
    # Tasks — per-row customization flags
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute(
        """
        ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS source_template_id VARCHAR(64)
        REFERENCES task_templates(id) ON DELETE SET NULL
        """
    )

    # ------------------------------------------------------------------
    # Sub-tasks — per-row customization flags
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE sub_tasks
        ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute(
        """
        ALTER TABLE sub_tasks
        ADD COLUMN IF NOT EXISTS source_template_id VARCHAR(64)
        REFERENCES sub_task_templates(id) ON DELETE SET NULL
        """
    )

    # ------------------------------------------------------------------
    # Checkpoints — per-row customization flags
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE checkpoints
        ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute(
        """
        ALTER TABLE checkpoints
        ADD COLUMN IF NOT EXISTS source_template_id VARCHAR(64)
        REFERENCES checkpoint_templates(id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    # Reverse strictly — drop owner indices + column. Keep the is_custom /
    # source_template_id columns on tasks/sub_tasks/checkpoints intact on
    # downgrade because the SQLAlchemy models still expect them; removing
    # them here would break application boot. Operators wanting a true
    # rollback should also revert the model definitions.
    op.execute("DROP INDEX IF EXISTS ix_plans_tenant_owner")
    op.execute("DROP INDEX IF EXISTS ix_plans_owner")
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS owner_id")
