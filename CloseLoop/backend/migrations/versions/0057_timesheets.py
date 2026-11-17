"""automated HRMS timesheet - project links, remembered mappings, submissions.

Six new tables, nothing existing is touched:

  * hrms_project_links       which entities' work belongs to an HRMS project
  * hrms_activity_mappings   task -> HRMS activity choices people confirmed
  * timesheet_submissions    one "Submit to HRMS" action (idempotent by key)
  * timesheet_lines          one HRMS entry; at most one live (PENDING / SENT /
                             UNCERTAIN) per user, project, day, activity and sub
                             activity - claimed before anything is sent
  * timesheet_line_sources   which time entries fed a line; time_entry_id is
                             UNIQUE so the same minutes can't be sent twice
  * timesheet_reminder_log   reminders already sent (restart-safe de-duplication)

`hrms_sub_activity_id` 0 means "no sub activity" - a real NULL can't take part
in a unique index.

Revision ID: 0057_timesheets
Revises: 0056_manual_holidays_2083
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057_timesheets"
down_revision: Union[str, None] = "0056_manual_holidays_2083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id() -> sa.Column:
    return sa.Column("id", sa.String(length=64), primary_key=True)


def _fk(name: str, target: str, *, nullable: bool = False, ondelete: str = "CASCADE"):
    return sa.Column(
        name,
        sa.String(length=64),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "hrms_project_links" not in existing:
        op.create_table(
            "hrms_project_links",
            _id(),
            _fk("tenant_id", "tenants.id"),
            sa.Column("hrms_project_id", sa.Integer(), nullable=False),
            sa.Column("hrms_project_name", sa.String(length=300), nullable=False),
            _fk("entity_id", "entities.id"),
            _fk("created_by_id", "users.id", nullable=True, ondelete="SET NULL"),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "tenant_id", "hrms_project_id", "entity_id", name="uq_hrms_project_link"
            ),
        )
        op.create_index(
            "ix_hrms_project_links_tenant_project",
            "hrms_project_links",
            ["tenant_id", "hrms_project_id"],
        )

    if "hrms_activity_mappings" not in existing:
        op.create_table(
            "hrms_activity_mappings",
            _id(),
            _fk("tenant_id", "tenants.id"),
            sa.Column("hrms_project_id", sa.Integer(), nullable=False),
            sa.Column("key_kind", sa.String(length=20), nullable=False),
            sa.Column("key_value", sa.String(length=300), nullable=False),
            sa.Column("hrms_activity_id", sa.Integer(), nullable=False),
            sa.Column(
                "hrms_sub_activity_id",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "confirmed_count", sa.Integer(), nullable=False, server_default="1"
            ),
            _fk("confirmed_by_id", "users.id", nullable=True, ondelete="SET NULL"),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "hrms_project_id",
                "key_kind",
                "key_value",
                name="uq_hrms_activity_mapping",
            ),
            sa.CheckConstraint(
                "key_kind IN ('template', 'title')", name="ck_hrms_map_kind"
            ),
        )

    if "timesheet_submissions" not in existing:
        op.create_table(
            "timesheet_submissions",
            _id(),
            _fk("tenant_id", "tenants.id"),
            _fk("user_id", "users.id"),
            sa.Column("hrms_project_id", sa.Integer(), nullable=False),
            sa.Column("hrms_project_name", sa.String(length=300), nullable=False),
            sa.Column("hrms_employee_id", sa.Integer(), nullable=False),
            sa.Column("range_start", sa.Date(), nullable=False),
            sa.Column("range_end", sa.Date(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=100), nullable=False),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="SUBMITTING"
            ),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "tenant_id", "user_id", "idempotency_key", name="uq_timesheet_idem"
            ),
        )
        op.create_index(
            "ix_timesheet_submissions_user",
            "timesheet_submissions",
            ["tenant_id", "user_id", "range_start"],
        )

    if "timesheet_lines" not in existing:
        op.create_table(
            "timesheet_lines",
            _id(),
            _fk("submission_id", "timesheet_submissions.id"),
            _fk("tenant_id", "tenants.id"),
            _fk("user_id", "users.id"),
            sa.Column("hrms_project_id", sa.Integer(), nullable=False),
            sa.Column("work_date", sa.Date(), nullable=False),
            sa.Column("hrms_activity_id", sa.Integer(), nullable=False),
            sa.Column(
                "hrms_sub_activity_id",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("minutes", sa.Integer(), nullable=False),
            sa.Column("hours", sa.Numeric(6, 2), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "work_type", sa.String(length=30), nullable=False, server_default="BILLABLE"
            ),
            sa.Column("match_tier", sa.String(length=30), nullable=True),
            sa.Column("match_score", sa.Numeric(4, 3), nullable=True),
            sa.Column("match_reason", sa.String(length=300), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="PENDING"
            ),
            sa.Column("hrms_entry_id", sa.String(length=100), nullable=True),
            sa.Column("error", sa.String(length=500), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_timesheet_lines_submission", "timesheet_lines", ["submission_id"]
        )
        op.create_index(
            "uq_timesheet_lines_live",
            "timesheet_lines",
            [
                "tenant_id",
                "user_id",
                "hrms_project_id",
                "work_date",
                "hrms_activity_id",
                "hrms_sub_activity_id",
            ],
            unique=True,
            postgresql_where=sa.text("status IN ('PENDING', 'SENT', 'UNCERTAIN')"),
        )

    if "timesheet_line_sources" not in existing:
        op.create_table(
            "timesheet_line_sources",
            _id(),
            _fk("line_id", "timesheet_lines.id"),
            _fk("time_entry_id", "time_entries.id", nullable=True, ondelete="SET NULL"),
            sa.Column("minutes", sa.Integer(), nullable=False),
            sa.UniqueConstraint("time_entry_id", name="uq_timesheet_source_entry"),
        )
        op.create_index(
            "ix_timesheet_sources_line", "timesheet_line_sources", ["line_id"]
        )

    if "timesheet_reminder_log" not in existing:
        op.create_table(
            "timesheet_reminder_log",
            _id(),
            _fk("tenant_id", "tenants.id"),
            _fk("user_id", "users.id"),
            sa.Column("calendar", sa.String(length=2), nullable=False),
            sa.Column("period_key", sa.String(length=10), nullable=False),
            sa.Column("stage", sa.String(length=20), nullable=False),
            sa.Column("sent_on", sa.Date(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "user_id",
                "calendar",
                "period_key",
                "stage",
                "sent_on",
                name="uq_timesheet_reminder",
            ),
        )


def downgrade() -> None:
    # Children first: sources -> lines -> submissions.
    for table in (
        "timesheet_reminder_log",
        "timesheet_line_sources",
        "timesheet_lines",
        "timesheet_submissions",
        "hrms_activity_mappings",
        "hrms_project_links",
    ):
        op.drop_table(table)
