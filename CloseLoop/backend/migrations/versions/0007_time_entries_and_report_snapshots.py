"""time entries + report snapshots (Task 3 Report Engine)

Adds the two new tables required by the Report Engine:

* ``time_entries`` — granular per-user time logged against a task / subtask
  / checkpoint. Drives the **User Workload and Progress Report** (2.3) and
  the **Time Entry Report** (2.5).
* ``report_snapshots`` — immutable, audit-grade record of every report the
  engine generates. Persisted on demand AND automatically when a period is
  closed (the ``Period Close Completion Report`` is snapshotted via the
  carry-forward flow so Super Admins and Team Leads can pull it later).

This migration is strictly additive — it does NOT touch the period
transition mechanism (P7 -> pre-seeded P8), the close-pack snapshot
generation pipeline, or the carry-forward queue rules. Uses
``CREATE TABLE IF NOT EXISTS`` and ``CREATE INDEX IF NOT EXISTS`` for
idempotency.

Revision ID: 0007_time_entries_and_report_snapshots
Revises: 0006_plan_ownership_and_custom_flags
Create Date: 2026-06-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0007_time_entries_and_report_snapshots"
down_revision: Union[str, None] = "0006_plan_ownership_and_custom_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # time_entries
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS time_entries (
            id               VARCHAR(64)   PRIMARY KEY,
            tenant_id        VARCHAR(64)   NOT NULL REFERENCES tenants(id)   ON DELETE CASCADE,
            user_id          VARCHAR(64)   NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            entity_id        VARCHAR(64)   NOT NULL REFERENCES entities(id)  ON DELETE CASCADE,
            task_id          VARCHAR(64)            REFERENCES tasks(id)         ON DELETE SET NULL,
            subtask_id       VARCHAR(64)            REFERENCES sub_tasks(id)     ON DELETE SET NULL,
            checkpoint_id    VARCHAR(64)            REFERENCES checkpoints(id)   ON DELETE SET NULL,
            period_id        VARCHAR(64)            REFERENCES periods(id)       ON DELETE SET NULL,
            entry_date       DATE          NOT NULL,
            started_at       TIMESTAMP WITH TIME ZONE,
            ended_at         TIMESTAMP WITH TIME ZONE,
            duration_minutes INTEGER       NOT NULL,
            note             TEXT,
            created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_tenant ON time_entries(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_user ON time_entries(user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_entity ON time_entries(entity_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_task ON time_entries(task_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_subtask ON time_entries(subtask_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_checkpoint ON time_entries(checkpoint_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_tenant_date "
        "ON time_entries(tenant_id, entry_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_tenant_user_date "
        "ON time_entries(tenant_id, user_id, entry_date)"
    )

    # ------------------------------------------------------------------
    # report_snapshots
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS report_snapshots (
            id              VARCHAR(64)   PRIMARY KEY,
            tenant_id       VARCHAR(64)   NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            report_type     VARCHAR(80)   NOT NULL,
            period_id       VARCHAR(64)            REFERENCES periods(id)  ON DELETE SET NULL,
            entity_id       VARCHAR(64)            REFERENCES entities(id) ON DELETE SET NULL,
            target_user_id  VARCHAR(64)            REFERENCES users(id)    ON DELETE SET NULL,
            generated_by_id VARCHAR(64)            REFERENCES users(id)    ON DELETE SET NULL,
            trigger         VARCHAR(40)   NOT NULL DEFAULT 'MANUAL',
            parameters      JSON,
            payload_json    JSON          NOT NULL,
            generated_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_snapshots_tenant ON report_snapshots(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_snapshots_tenant_type "
        "ON report_snapshots(tenant_id, report_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_snapshots_period ON report_snapshots(period_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_snapshots_tenant_generated "
        "ON report_snapshots(tenant_id, generated_at)"
    )


def downgrade() -> None:
    # Drop indices then tables (strict reverse).
    op.execute("DROP INDEX IF EXISTS ix_report_snapshots_tenant_generated")
    op.execute("DROP INDEX IF EXISTS ix_report_snapshots_period")
    op.execute("DROP INDEX IF EXISTS ix_report_snapshots_tenant_type")
    op.execute("DROP INDEX IF EXISTS ix_report_snapshots_tenant")
    op.execute("DROP TABLE IF EXISTS report_snapshots")

    op.execute("DROP INDEX IF EXISTS ix_time_entries_tenant_user_date")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_tenant_date")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_checkpoint")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_subtask")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_task")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_entity")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_user")
    op.execute("DROP INDEX IF EXISTS ix_time_entries_tenant")
    op.execute("DROP TABLE IF EXISTS time_entries")
