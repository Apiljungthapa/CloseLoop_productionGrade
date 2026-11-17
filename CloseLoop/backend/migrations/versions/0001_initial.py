"""initial schema — CloseLoop PHASE 1

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Postgres enum type names (defined once, reused)
_tenant_status = sa.Enum("active", "suspended", name="tenant_status")
_user_role = sa.Enum(
    "PLATFORM_OWNER",
    "SUPER_ADMIN",
    "GROUP_MANAGER",
    "TEAM_LEAD",
    "TEAM_MEMBER",
    name="user_role",
)
_entity_group_type = sa.Enum(
    "REGION",
    "DIVISION",
    "DEPARTMENT",
    "SUBSIDIARY",
    "BUSINESS_UNIT",
    "CUSTOM",
    name="entity_group_type",
)
_period_status = sa.Enum(
    "OPEN", "IN_PROGRESS", "CLOSED", "ARCHIVED", name="period_status"
)
_close_pack_type = sa.Enum(
    "ANNUAL",
    "SEMI_ANNUAL",
    "QUARTERLY",
    "MONTHLY",
    "FOUR_WEEK",
    "BI_WEEKLY",
    "WEEKLY",
    name="close_pack_type",
)
_plan_status = sa.Enum("OPEN", "IN_PROGRESS", "CLOSED", name="plan_status")
_task_status = sa.Enum(
    "NOT_STARTED",
    "IN_PROGRESS",
    "PENDING_REVIEW",
    "COMPLETED",
    "REJECTED",
    "CARRIED_FORWARD",
    name="task_status",
)
_cf_status = sa.Enum("PENDING", "APPROVED", "REJECTED", name="carry_forward_status")


def upgrade() -> None:
    # ---- tenants ----
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "status",
            _tenant_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column("plan_limit", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # ---- entity_groups (recursive) ----
    op.create_table(
        "entity_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(64),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "type",
            _entity_group_type,
            nullable=False,
            server_default="CUSTOM",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_entity_groups_tenant", "entity_groups", ["tenant_id"])
    op.create_index("ix_entity_groups_parent", "entity_groups", ["parent_id"])
    op.create_index(
        "ix_entity_groups_tenant_parent", "entity_groups", ["tenant_id", "parent_id"]
    )

    # ---- entities ----
    op.create_table(
        "entities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_entities_tenant", "entities", ["tenant_id"])
    op.create_index("ix_entities_group", "entities", ["group_id"])
    op.create_index("ix_entities_tenant_group", "entities", ["tenant_id", "group_id"])

    # ---- users ----
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", _user_role, nullable=False),
        sa.Column(
            "primary_entity_id",
            sa.String(64),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("invited_by_id", sa.String(64), nullable=True),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )
    op.create_index("ix_users_tenant", "users", ["tenant_id"])
    op.create_index("ix_users_tenant_role", "users", ["tenant_id", "role"])

    # ---- user_entity_assignments ----
    op.create_table(
        "user_entity_assignments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "entity_id", name="uq_user_entity"),
    )
    op.create_index("ix_uea_tenant", "user_entity_assignments", ["tenant_id"])
    op.create_index("ix_uea_user", "user_entity_assignments", ["user_id"])
    op.create_index("ix_uea_entity", "user_entity_assignments", ["entity_id"])

    # ---- user_entity_group_assignments ----
    op.create_table(
        "user_entity_group_assignments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "group_id", name="uq_user_entity_group"),
    )
    op.create_index("ix_uega_tenant", "user_entity_group_assignments", ["tenant_id"])
    op.create_index("ix_uega_user", "user_entity_group_assignments", ["user_id"])
    op.create_index("ix_uega_group", "user_entity_group_assignments", ["group_id"])

    # ---- period_configurations ----
    op.create_table(
        "period_configurations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "start_day_of_week", sa.Integer(), nullable=False, server_default="4"
        ),
        sa.Column("duration_weeks", sa.Integer(), nullable=False, server_default="4"),
        sa.Column(
            "auto_generate", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "carry_forward_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", name="uq_period_config_tenant"),
    )
    op.create_index("ix_period_config_tenant", "period_configurations", ["tenant_id"])

    # ---- periods ----
    op.create_table(
        "periods",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", _period_status, nullable=False, server_default="OPEN"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "next_period_id",
            sa.String(64),
            sa.ForeignKey("periods.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "label", name="uq_period_label_tenant"),
    )
    op.create_index("ix_periods_tenant", "periods", ["tenant_id"])
    op.create_index("ix_periods_tenant_status", "periods", ["tenant_id", "status"])

    # ---- close_packs ----
    op.create_table(
        "close_packs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", _close_pack_type, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_close_packs_tenant", "close_packs", ["tenant_id"])
    op.create_index(
        "ix_close_packs_tenant_active", "close_packs", ["tenant_id", "active"]
    )

    # ---- close_pack_versions ----
    op.create_table(
        "close_pack_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "close_pack_id",
            sa.String(64),
            sa.ForeignKey("close_packs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_by_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("close_pack_id", "version_number", name="uq_pack_version"),
    )
    op.create_index("ix_pack_versions_pack", "close_pack_versions", ["close_pack_id"])
    op.create_index("ix_pack_versions_tenant", "close_pack_versions", ["tenant_id"])

    # ---- task_templates ----
    op.create_table(
        "task_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "close_pack_id",
            sa.String(64),
            sa.ForeignKey("close_packs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "due_day_of_period", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "reviewer_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_task_templates_pack", "task_templates", ["close_pack_id"])
    op.create_index("ix_task_templates_tenant", "task_templates", ["tenant_id"])

    # ---- sub_task_templates ----
    op.create_table(
        "sub_task_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_template_id",
            sa.String(64),
            sa.ForeignKey("task_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_day_of_period", sa.Integer(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_subtask_templates_task", "sub_task_templates", ["task_template_id"]
    )

    # ---- checkpoint_templates ----
    op.create_table(
        "checkpoint_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sub_task_template_id",
            sa.String(64),
            sa.ForeignKey("sub_task_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "evidence_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("count_required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_checkpoint_templates_subtask",
        "checkpoint_templates",
        ["sub_task_template_id"],
    )

    # ---- plans ----
    op.create_table(
        "plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "period_id",
            sa.String(64),
            sa.ForeignKey("periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_pack_id",
            sa.String(64),
            sa.ForeignKey("close_packs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_pack_version", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", _plan_status, nullable=False, server_default="OPEN"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_plans_tenant", "plans", ["tenant_id"])
    op.create_index("ix_plans_period", "plans", ["period_id"])
    op.create_index("ix_plans_entity", "plans", ["entity_id"])
    op.create_index("ix_plans_tenant_period", "plans", ["tenant_id", "period_id"])
    op.create_index("ix_plans_tenant_status", "plans", ["tenant_id", "status"])

    # ---- tasks ----
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_to_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "reviewer_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", _task_status, nullable=False, server_default="NOT_STARTED"),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tasks_tenant", "tasks", ["tenant_id"])
    op.create_index("ix_tasks_plan", "tasks", ["plan_id"])
    op.create_index("ix_tasks_entity", "tasks", ["entity_id"])
    op.create_index("ix_tasks_assigned", "tasks", ["assigned_to_id"])
    op.create_index("ix_tasks_tenant_status", "tasks", ["tenant_id", "status"])
    op.create_index("ix_tasks_tenant_due", "tasks", ["tenant_id", "due_date"])

    # ---- sub_tasks ----
    op.create_table(
        "sub_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(64),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_to_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reviewer_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", _task_status, nullable=False, server_default="NOT_STARTED"),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_subtasks_tenant", "sub_tasks", ["tenant_id"])
    op.create_index("ix_subtasks_task", "sub_tasks", ["task_id"])
    op.create_index("ix_subtasks_assigned", "sub_tasks", ["assigned_to_id"])
    op.create_index("ix_subtasks_reviewer", "sub_tasks", ["reviewer_id"])
    op.create_index("ix_subtasks_tenant_status", "sub_tasks", ["tenant_id", "status"])

    # ---- checkpoints ----
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sub_task_id",
            sa.String(64),
            sa.ForeignKey("sub_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "evidence_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("count_required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "completed_by_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_checkpoints_tenant", "checkpoints", ["tenant_id"])
    op.create_index("ix_checkpoints_subtask", "checkpoints", ["sub_task_id"])

    # ---- attachments ----
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_id",
            sa.String(64),
            sa.ForeignKey("checkpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_key", sa.String(500), nullable=False),
        sa.Column(
            "uploaded_by_id",
            sa.String(64),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_attachments_checkpoint", "attachments", ["checkpoint_id"])

    # ---- carry_forward_groups ----
    op.create_table(
        "carry_forward_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_period_id",
            sa.String(64),
            sa.ForeignKey("periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_period_id",
            sa.String(64),
            sa.ForeignKey("periods.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", _cf_status, nullable=False, server_default="PENDING"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewed_by_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_cfg_tenant", "carry_forward_groups", ["tenant_id"])
    op.create_index("ix_cfg_from_period", "carry_forward_groups", ["from_period_id"])
    op.create_index("ix_cfg_to_period", "carry_forward_groups", ["to_period_id"])
    op.create_index("ix_cfg_status", "carry_forward_groups", ["tenant_id", "status"])

    # ---- carry_forward_items ----
    op.create_table(
        "carry_forward_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("carry_forward_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_plan_id", sa.String(64), nullable=False),
        sa.Column("original_task_id", sa.String(64), nullable=True),
        sa.Column("original_subtask_id", sa.String(64), nullable=True),
        sa.Column("original_assignee_id", sa.String(64), nullable=True),
        sa.Column("original_entity_id", sa.String(64), nullable=False),
        sa.Column("item_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("copied_to_task_id", sa.String(64), nullable=True),
        sa.Column("copied_to_subtask_id", sa.String(64), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_cfi_group", "carry_forward_items", ["group_id"])
    op.create_index("ix_cfi_tenant", "carry_forward_items", ["tenant_id"])

    # ---- audit_logs ----
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_tenant", "audit_logs", ["tenant_id"])
    op.create_index(
        "ix_audit_tenant_created", "audit_logs", ["tenant_id", "created_at"]
    )
    op.create_index("ix_audit_entity", "audit_logs", ["entity_type", "entity_id"])

    # ---- notifications ----
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_notifications_user", "notifications", ["tenant_id", "user_id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("audit_logs")
    op.drop_table("carry_forward_items")
    op.drop_table("carry_forward_groups")
    op.drop_table("attachments")
    op.drop_table("checkpoints")
    op.drop_table("sub_tasks")
    op.drop_table("tasks")
    op.drop_table("plans")
    op.drop_table("checkpoint_templates")
    op.drop_table("sub_task_templates")
    op.drop_table("task_templates")
    op.drop_table("close_pack_versions")
    op.drop_table("close_packs")
    op.drop_table("periods")
    op.drop_table("period_configurations")
    op.drop_table("user_entity_group_assignments")
    op.drop_table("user_entity_assignments")
    op.drop_table("users")
    op.drop_table("entities")
    op.drop_table("entity_groups")
    op.drop_table("tenants")

    # drop enum types
    _cf_status.drop(op.get_bind(), checkfirst=True)
    _task_status.drop(op.get_bind(), checkfirst=True)
    _plan_status.drop(op.get_bind(), checkfirst=True)
    _close_pack_type.drop(op.get_bind(), checkfirst=True)
    _period_status.drop(op.get_bind(), checkfirst=True)
    _entity_group_type.drop(op.get_bind(), checkfirst=True)
    _user_role.drop(op.get_bind(), checkfirst=True)
    _tenant_status.drop(op.get_bind(), checkfirst=True)
