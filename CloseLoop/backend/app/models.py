"""
CloseLoop — SQLAlchemy 2.0 async models (PHASE 1).

Multi-tenant Financial Close Management. Every business table carries
`tenant_id` for row-level isolation. Hierarchical entity groups, close
pack templates with versioning, tenant-wide periods, plan generation,
carry-forward, and immutable audit logs.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, date, time, timezone
from typing import Optional, List

from sqlalchemy import (
    String,
    Integer,
    Numeric,
    DateTime,
    Date,
    Time,
    ForeignKey,
    Boolean,
    Text,
    JSON,
    UniqueConstraint,
    CheckConstraint,
    Index,
    Enum as SAEnum,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.state import LoaderCallableStatus
from sqlalchemy.sql import func
from sqlalchemy import inspect
from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ============================================================
# Enums
# ============================================================
class Role(str, enum.Enum):
    PLATFORM_OWNER = "PLATFORM_OWNER"
    SUPER_ADMIN = "SUPER_ADMIN"
    TEAM_LEAD = "TEAM_LEAD"
    TEAM_MEMBER = "TEAM_MEMBER"


class TenantStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class EntityGroupType(str, enum.Enum):
    REGION = "REGION"
    DIVISION = "DIVISION"
    DEPARTMENT = "DEPARTMENT"
    SUBSIDIARY = "SUBSIDIARY"
    BUSINESS_UNIT = "BUSINESS_UNIT"
    CUSTOM = "CUSTOM"


class PeriodStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


class PeriodCadence(str, enum.Enum):
    """How a tenant's periods are shaped. FIXED_WEEKS is the original
    fixed-N-week "retail calendar" pattern (start_day_of_week +
    duration_weeks); CALENDAR_MONTH / CALENDAR_QUARTER / CALENDAR_HALF_YEAR
    follow real Gregorian (AD) calendar boundaries (variable day counts).
    BS_MONTH / BS_QUARTER / BS_HALF_YEAR are the Bikram Sambat (Nepali)
    equivalents, anchored to Nepal's Shrawan-to-Ashadh fiscal year — see
    app/calendar/. All BS_* cadences still store plain AD start/end dates
    on Period; BS is derived on read for labelling and display only."""

    FIXED_WEEKS = "FIXED_WEEKS"
    CALENDAR_MONTH = "CALENDAR_MONTH"
    CALENDAR_QUARTER = "CALENDAR_QUARTER"
    CALENDAR_HALF_YEAR = "CALENDAR_HALF_YEAR"
    CALENDAR_YEAR = "CALENDAR_YEAR"
    BS_MONTH = "BS_MONTH"
    BS_QUARTER = "BS_QUARTER"
    BS_HALF_YEAR = "BS_HALF_YEAR"
    BS_YEAR = "BS_YEAR"


class CalendarType(str, enum.Enum):
    """Which calendar a Close Pack's periods, task schedules, and reporting
    follow — replaces the old ClosePackType (DAILY/WEEKLY/.../ANNUAL) label.
    AD = Gregorian. BS = Bikram Sambat (Nepali) — see app/calendar/."""

    AD = "AD"
    BS = "BS"


class PlanStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class TaskStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CARRIED_FORWARD = "CARRIED_FORWARD"
    # A task materialized into a period that is not yet the running one.
    # Visible to the Lead but not assignable / editable until that period
    # becomes current (see routers/tasks.py `_ensure_not_upcoming`).
    UPCOMING = "UPCOMING"


class RecurrenceFrequency(str, enum.Enum):
    """How often a Close Pack task recurs.

    NULL on a template = "once per period".

    DAILY / WEEKLY are SUB-period — they produce many Task instances inside
    each period (one per matching date): DAILY every `interval_count` days,
    WEEKLY on the `days_of_week` weekdays every `interval_count` weeks.

    MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL are PERIOD-level — one Task
    in each period the cycle lands on (assuming ~monthly periods: every
    1st / 3rd / 6th / 12th period). See services/recurrence.py.

    "Custom" is not a separate value: it is any frequency combined with an
    `interval_count > 1` (or, for WEEKLY, a hand-picked `days_of_week`).
    `interval_count` multiplies the base step for every frequency — e.g.
    MONTHLY x2 = every other month, QUARTERLY x2 = every 6 months.
    """

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    ANNUAL = "ANNUAL"


class DueAnchor(str, enum.Enum):
    """Which end of the period a template's `due_day_of_period` counts from.

    PERIOD_START — day 1 = the period's first day  (start + N-1 days)
    PERIOD_END   — day 1 = the period's last day   (end - N-1 days),
                   i.e. "N days from close"
    """

    PERIOD_START = "PERIOD_START"
    PERIOD_END = "PERIOD_END"


class DueDateType(str, enum.Enum):
    """Whether a task/sub-task's due date must land inside its owning
    period, or may run on past the period's end date.

    WITHIN_PERIOD    — due_date must be <= period.end_date (the default,
                        and the only mode that existed before this field).
    AFTER_PERIOD_END — due_date may run up to `post_period_due_days` days
                        past period.end_date. The period itself is NEVER
                        extended and the task's period_id never changes —
                        a P1 task due after P1 closes is still a P1 task.
                        See services/due_date_rules.py for the shared
                        validation/calculation this drives everywhere.
    """

    WITHIN_PERIOD = "WITHIN_PERIOD"
    AFTER_PERIOD_END = "AFTER_PERIOD_END"


class CarryForwardStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Priority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ============================================================
# Tenant
# ============================================================
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # URL-safe identifier — the tenant-scoped segment of every in-app route
    # (https://.../<slug>/dashboard, .../<slug>/packs/123, …). Distinct from
    # `name` (display label, free text) so renaming a tenant doesn't break
    # every bookmarked/shared link. See services/tenant_slug_service.py for
    # generation rules (reserved words, uniqueness).
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    status: Mapped[TenantStatus] = mapped_column(
        SAEnum(TenantStatus, name="tenant_status"), default=TenantStatus.active
    )
    plan_limit: Mapped[int] = mapped_column(Integer, default=1000)
    # Per-tenant OAuth2 client-credentials pair for machine-to-machine API
    # access (integrations/scripts) — entirely separate from human login,
    # which stays email+password. Auto-generated when the tenant is
    # created (see tenants.py); only the secret's hash is ever persisted,
    # mirroring RefreshToken's hash-only storage.
    client_id: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    client_secret_hash: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    client_secret_rotated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ============================================================
# Organization hierarchy
# ============================================================
class EntityGroup(Base):
    """Recursive group hierarchy. EntityGroup can contain other EntityGroups."""

    __tablename__ = "entity_groups"
    __table_args__ = (
        Index("ix_entity_groups_tenant", "tenant_id"),
        Index("ix_entity_groups_parent", "parent_id"),
        Index("ix_entity_groups_tenant_parent", "tenant_id", "parent_id"),
        Index("ix_entity_groups_tenant_active", "tenant_id", "is_active"),
        Index("ix_entity_groups_tenant_name", "tenant_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("entity_groups.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[EntityGroupType] = mapped_column(
        SAEnum(EntityGroupType, name="entity_group_type"),
        nullable=False,
        default=EntityGroupType.CUSTOM,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent: Mapped[Optional["EntityGroup"]] = relationship(
        "EntityGroup", remote_side=[id], back_populates="children"
    )
    children: Mapped[List["EntityGroup"]] = relationship(
        "EntityGroup", back_populates="parent", cascade="all,delete-orphan"
    )
    entities: Mapped[List["Entity"]] = relationship(
        back_populates="group", cascade="all,delete-orphan"
    )
    entity_memberships: Mapped[List["EntityGroupMembership"]] = relationship(
        back_populates="group", cascade="all,delete-orphan"
    )
    member_entities: Mapped[List["Entity"]] = relationship(
        secondary="entity_group_memberships",
        back_populates="groups",
        viewonly=True,
    )


class Entity(Base):
    """Leaf node — every Plan/Task is bound to an Entity."""

    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entities_tenant", "tenant_id"),
        Index("ix_entities_group", "group_id"),
        Index("ix_entities_tenant_group", "tenant_id", "group_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entity_groups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    group: Mapped["EntityGroup"] = relationship(back_populates="entities")
    group_memberships: Mapped[List["EntityGroupMembership"]] = relationship(
        back_populates="entity", cascade="all,delete-orphan"
    )
    groups: Mapped[List["EntityGroup"]] = relationship(
        secondary="entity_group_memberships",
        back_populates="member_entities",
        viewonly=True,
    )

    @property
    def group_ids(self) -> list[str]:
        ids: list[str] = []
        state = inspect(self)
        groups_value = state.attrs.groups.loaded_value
        memberships_value = state.attrs.group_memberships.loaded_value

        if groups_value is not LoaderCallableStatus.NO_VALUE:
            ids = [g.id for g in groups_value or [] if g is not None]
        elif memberships_value is not LoaderCallableStatus.NO_VALUE:
            ids = [m.group_id for m in memberships_value or [] if m is not None]

        if self.group_id and self.group_id not in ids:
            ids.insert(0, self.group_id)
        return ids


class EntityGroupMembership(Base):
    __tablename__ = "entity_group_memberships"
    __table_args__ = (
        UniqueConstraint("entity_id", "group_id", name="uq_entity_group_membership"),
        Index("ix_entity_group_memberships_tenant", "tenant_id"),
        Index("ix_entity_group_memberships_entity", "entity_id"),
        Index("ix_entity_group_memberships_group", "group_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entity_groups.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped["Entity"] = relationship(back_populates="group_memberships")
    group: Mapped["EntityGroup"] = relationship(back_populates="entity_memberships")


# ============================================================
# Users + access
# ============================================================
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
        Index("ix_users_tenant", "tenant_id"),
        Index("ix_users_tenant_role", "tenant_id", "role"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="user_role"), nullable=False)
    # Job title/designation (e.g. "Finance Controller", "Ledger Accountant")
    # — purely cosmetic, never read for authorization. `role` alone still
    # drives every is_admin/is_team_lead/is_team_member permission check;
    # this just lets a tenant label people more specifically than the 4
    # coarse permission tiers without touching any access-control logic.
    title: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    primary_entity_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True
    )
    invited_by_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # lazy="selectin" (not this codebase's usual explicit .options() —
    # relationship is read from several call sites: login, /auth/me,
    # /auth/refresh) so `.tenant` is always safe to touch without every
    # caller remembering to eager-load it under AsyncSession.
    tenant: Mapped[Optional["Tenant"]] = relationship(lazy="selectin")

    @property
    def tenant_slug(self) -> Optional[str]:
        """URL-safe org segment for this user's tenant — None for a
        Platform Owner (no tenant_id) or during signup races before the
        row is committed."""
        return self.tenant.slug if self.tenant else None


class RefreshToken(Base):
    """Server-side record of an issued refresh token so logout / revocation
    actually invalidates the session instead of merely clearing the client.
    Only the SHA-256 hash of the token is stored — the raw value lives only
    in the httpOnly cookie."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user", "user_id"),
        Index("ix_refresh_tokens_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PasswordResetToken(Base):
    """"Forgot password" token — same hash-only storage pattern as
    RefreshToken. The raw value only ever exists in the emailed link;
    `token_hash` is what's looked up on redemption. `used_at` makes a
    token single-use even before it expires."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserEntityAssignment(Base):
    """Extra (non-primary) entities the user can act on."""

    __tablename__ = "user_entity_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_id", name="uq_user_entity"),
        Index("ix_uea_tenant", "tenant_id"),
        Index("ix_uea_user", "user_id"),
        Index("ix_uea_entity", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserEntityGroupAssignment(Base):
    """Group managers inherit access to all descendant groups + entities."""

    __tablename__ = "user_entity_group_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_user_entity_group"),
        Index("ix_uega_tenant", "tenant_id"),
        Index("ix_uega_user", "user_id"),
        Index("ix_uega_group", "group_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entity_groups.id", ondelete="CASCADE"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ============================================================
# Period management (tenant-wide)
# ============================================================
class PeriodConfiguration(Base):
    __tablename__ = "period_configurations"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_period_config_tenant"),
        Index("ix_period_config_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # How this tenant's periods are shaped — see PeriodCadence. Defaults to
    # the original fixed-week pattern so existing tenants are unaffected.
    cadence: Mapped[PeriodCadence] = mapped_column(
        SAEnum(PeriodCadence, name="period_cadence"),
        default=PeriodCadence.FIXED_WEEKS,
        nullable=False,
    )
    # 0=Mon .. 4=Fri (default Friday) — only meaningful for FIXED_WEEKS.
    start_day_of_week: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    duration_weeks: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    carry_forward_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Period(Base):
    __tablename__ = "periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "label", name="uq_period_label_tenant"),
        Index("ix_periods_tenant", "tenant_id"),
        Index("ix_periods_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        SAEnum(PeriodStatus, name="period_status"), default=PeriodStatus.OPEN
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    next_period_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("periods.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ============================================================
# Close Pack system + versioning
# ============================================================
class ClosePack(Base):
    """Reusable template for generating plans. Versioned."""

    __tablename__ = "close_packs"
    __table_args__ = (
        Index("ix_close_packs_tenant", "tenant_id"),
        Index("ix_close_packs_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Which calendar every period/date/schedule/report generated from this
    # pack follows. Replaces the old fixed "pack type" (DAILY/WEEKLY/.../
    # ANNUAL) label — scheduling cadence now lives per-task (see
    # TaskTemplate.frequency below), independent of the pack itself.
    calendar_type: Mapped[CalendarType] = mapped_column(
        SAEnum(CalendarType, name="calendar_type"),
        default=CalendarType.AD,
        server_default="AD",
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClosePackVersion(Base):
    """Immutable snapshot of a ClosePack's template tree at a point in time."""

    __tablename__ = "close_pack_versions"
    __table_args__ = (
        UniqueConstraint("close_pack_id", "version_number", name="uq_pack_version"),
        Index("ix_pack_versions_pack", "close_pack_id"),
        Index("ix_pack_versions_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    close_pack_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("close_packs.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ----- Template hierarchy (editable working copy) -----
class TaskTemplate(Base):
    __tablename__ = "task_templates"
    __table_args__ = (
        Index("ix_task_templates_pack", "close_pack_id"),
        Index("ix_task_templates_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    close_pack_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("close_packs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # "Day N of the period" — resolved against each generation period via
    # services.recurrence.due_within(). With due_anchor = PERIOD_START a
    # generated Task's due date is period_start + (N-1); with PERIOD_END
    # it's period_end - (N-1) ("N days from close"). Clamped to the period.
    due_day_of_period: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    due_anchor: Mapped[DueAnchor] = mapped_column(
        SAEnum(DueAnchor, name="due_anchor"),
        default=DueAnchor.PERIOD_START,
        server_default="PERIOD_START",
        nullable=False,
    )
    # Whether a Task generated from this template must fall inside its
    # period (WITHIN_PERIOD, the default) or may run up to
    # `post_period_due_days` days past the period's end (AFTER_PERIOD_END).
    # See DueDateType + services/due_date_rules.py.
    due_date_type: Mapped[DueDateType] = mapped_column(
        SAEnum(DueDateType, name="due_date_type"),
        default=DueDateType.WITHIN_PERIOD,
        server_default="WITHIN_PERIOD",
        nullable=False,
    )
    post_period_due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # How often this task recurs. NULL = once per period (see
    # services/recurrence.py and the RecurrenceFrequency docstring).
    frequency: Mapped[Optional[RecurrenceFrequency]] = mapped_column(
        SAEnum(RecurrenceFrequency, name="recurrence_frequency"), nullable=True
    )
    # Required vs Optional — ONLY meaningful for a DAILY-frequency template
    # right now (validated in schemas.TaskTemplateIn). True (default) is
    # the behavior every template had before this field existed: an
    # uncompleted instance becomes overdue after its due date. False lets a
    # daily occurrence be skipped/left uncompleted without ever counting as
    # overdue or against completion compliance — it's still generated and
    # visible, just exempt from the overdue check everywhere that computes
    # it (dashboards, task board, reports). See _bucket_for_task /
    # taskStatusKey / report_service's overdue queries.
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # "every N days" (DAILY) / "every N weeks" (WEEKLY).
    interval_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    # Weekdays a WEEKLY task fires on, 0=Mon .. 6=Sun.
    days_of_week: Mapped[Optional[List[int]]] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    # Recurrence anchor: the FIRST occurrence date. Every later occurrence
    # is computed forward from here (see services/recurrence.py) and dropped
    # into whichever tenant period contains it. Only used when `frequency`
    # is set; a non-recurring task uses `due_day_of_period` instead.
    recurrence_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    recurrence_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # N-Day Due Date — ONLY meaningful when `frequency` is set (every
    # recurring cadence: DAILY/WEEKLY/MONTHLY/QUARTERLY/SEMI_ANNUAL/ANNUAL,
    # including a "Custom" interval): each generated occurrence's due_date
    # = the occurrence's own landing date + due_days, computed fresh per
    # occurrence (Sep 1 + 5 -> due Sep 6, Oct 1 + 5 -> due Oct 6, ...) —
    # NOT the period's end, and never waiting for the next occurrence.
    # 0 = due on the occurrence date itself (default; matches every
    # existing recurring template's behavior before this field existed).
    # Ignored for a non-recurring ("Once per Period") template, which uses
    # due_day_of_period/due_anchor or due_date_type/post_period_due_days
    # instead. See services/due_date_rules.py.
    due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    due_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    reviewer_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # Default tags/priority carried onto every Task generated from this
    # template — same fields as Task.tags/.priority, just settable up front
    # instead of per-generated-task.
    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default="{}", nullable=False
    )
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, name="priority"), default=Priority.MEDIUM, nullable=False
    )
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sub_task_templates: Mapped[List["SubTaskTemplate"]] = relationship(
        back_populates="task_template",
        cascade="all,delete-orphan",
        order_by="SubTaskTemplate.order",
    )


class SubTaskTemplate(Base):
    __tablename__ = "sub_task_templates"
    __table_args__ = (Index("ix_subtask_templates_task", "task_template_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_template_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task_templates.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Day N of the period, resolved via due_within() against `due_anchor`;
    # NULL falls back to the parent task's own resolved due date. A
    # sub-task's generated due date is always clamped to be on/before its
    # parent task's.
    due_day_of_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    due_anchor: Mapped[DueAnchor] = mapped_column(
        SAEnum(DueAnchor, name="due_anchor"),
        default=DueAnchor.PERIOD_START,
        server_default="PERIOD_START",
        nullable=False,
    )
    # Same WITHIN_PERIOD/AFTER_PERIOD_END window as TaskTemplate, applied
    # to this sub-task's own resolved due date — always additionally capped
    # at the parent task's own due date (see services/due_date_rules.py).
    due_date_type: Mapped[DueDateType] = mapped_column(
        SAEnum(DueDateType, name="due_date_type"),
        default=DueDateType.WITHIN_PERIOD,
        server_default="WITHIN_PERIOD",
        nullable=False,
    )
    post_period_due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    due_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    # A sub-task can recur independently of its parent task, generating one
    # SubTask row per occurrence UNDER EACH parent task occurrence — e.g. a
    # DAILY sub-task under one MONTHLY task materializes ~30 sub-task rows
    # for that one task. NULL = single sub-task (the pre-existing
    # behavior); ignored when the parent task ITSELF is DAILY/WEEKLY (that
    # already multiplies 1:1, so a sub-task frequency would only cause the
    # window to grow across every one of the parent's own occurrences —
    # see plan_generation_service._subtask_dates). Same field shapes as
    # TaskTemplate's own frequency columns.
    frequency: Mapped[Optional[RecurrenceFrequency]] = mapped_column(
        SAEnum(RecurrenceFrequency, name="recurrence_frequency"), nullable=True
    )
    interval_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    days_of_week: Mapped[Optional[List[int]]] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    # First occurrence; NULL defaults to the period start at generation
    # time. Every later occurrence is computed forward from here, capped
    # at the parent task's own due date (and at recurrence_end, if set).
    recurrence_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    recurrence_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # N-Day Due Date for THIS sub-task's own recurrence (only meaningful
    # when this row's own `frequency` is set — see TaskTemplate.due_days).
    # due_date = this occurrence's own landing date + due_days, always
    # additionally capped so it never exceeds the parent task's own due
    # date (see plan_generation_service._subtask_dates).
    due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task_template: Mapped["TaskTemplate"] = relationship(
        back_populates="sub_task_templates"
    )


# ============================================================
# Generated plans (from ClosePack)
# ============================================================
class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        Index("ix_plans_tenant", "tenant_id"),
        Index("ix_plans_period", "period_id"),
        Index("ix_plans_entity", "entity_id"),
        Index("ix_plans_tenant_period", "tenant_id", "period_id"),
        Index("ix_plans_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    period_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("periods.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    source_pack_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("close_packs.id", ondelete="SET NULL"), nullable=True
    )
    source_pack_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Explicit Plan Ownership — set by an Admin (Tier 1) to hand the plan to
    # a specific Team Lead (Tier 2). The owner is the structural authority
    # for the plan even if their entity scope changes. Customization-layer
    # permission checks AND this field (admin OR plan owner OR entity scope).
    owner_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[PlanStatus] = mapped_column(
        SAEnum(PlanStatus, name="plan_status"), default=PlanStatus.OPEN
    )
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tasks: Mapped[List["Task"]] = relationship(
        back_populates="plan", cascade="all,delete-orphan"
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_tenant", "tenant_id"),
        Index("ix_tasks_plan", "plan_id"),
        Index("ix_tasks_entity", "entity_id"),
        Index("ix_tasks_assigned", "assigned_to_id"),
        Index("ix_tasks_member_assigned", "member_assigned_to_id"),
        Index("ix_tasks_tenant_status", "tenant_id", "status"),
        Index("ix_tasks_tenant_due", "tenant_id", "due_date"),
        Index("ix_tasks_tags", "tags", postgresql_using="gin"),
        Index("ix_tasks_recurring_group", "recurring_group_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    source_template_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("task_templates.id", ondelete="SET NULL"), nullable=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Shared id across every period-instance of ONE recurring custom task a
    # Lead/Admin created via "Add Task" with a frequency. NULL for
    # template-sourced tasks (they link by source_template_id) and for
    # plain one-off custom tasks.
    recurring_group_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # The recurrence definition for a custom recurring task, copied onto
    # every instance in the group: {frequency, interval_count, days_of_week,
    # anchor, end, calendar, title, description, subtasks, source_pack_id}.
    # The period-close scheduler reads this to materialise the NEXT
    # occurrence when the horizon rolls forward. NULL for template-sourced
    # and non-recurring tasks.
    recurrence_rule: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # The Team Lead who owns/manages this task (assigned by an Admin, or the
    # Lead who created the custom task).
    assigned_to_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The Team Member who executes this task, ONLY meaningful when the task
    # is a leaf (no sub-tasks) — the Lead assigns this once they own the
    # task. Completion for a leaf task always flows through this person:
    # they submit-for-review, the Lead approves/rejects.
    member_assigned_to_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Whole-entity assignment mode — mutually exclusive with
    # member_assigned_to_id. When True, every TEAM_MEMBER scoped to this
    # task's entity gets a TaskEntityAssignee row and must individually
    # mark themselves done; the task only auto-submits for the Lead's
    # review once every row is done (see routers/tasks.py).
    assigned_entity_wide: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Copied from the source TaskTemplate (or, for a custom recurring task,
    # from the create payload) at generation time — see TaskTemplate's own
    # is_required for what this actually does. Independent of the template
    # afterward EXCEPT that an admin's template edit re-pushes it onto every
    # not-yet-started instance, same as any other synced template field.
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Whether `due_date` must fall inside `plan.period` (WITHIN_PERIOD, the
    # default — every task created before this field existed behaves this
    # way) or may run up to `post_period_due_days` days past the period's
    # end (AFTER_PERIOD_END). The period itself is never extended and this
    # task's plan/period never changes because of it — see DueDateType and
    # services/due_date_rules.py, the single source of truth for validating
    # and computing this window everywhere (forms, generation, carry-
    # forward, dashboards).
    due_date_type: Mapped[DueDateType] = mapped_column(
        SAEnum(DueDateType, name="due_date_type"),
        default=DueDateType.WITHIN_PERIOD,
        server_default="WITHIN_PERIOD",
        nullable=False,
    )
    post_period_due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # The raw calendar date THIS RECURRING instance actually landed on —
    # e.g. the Sep 1 occurrence of a MONTHLY task — before any N-Day Due
    # Date offset. NULL for a non-recurring ("Once per Period") task and
    # for a plain one-off custom task; always set for a template- or
    # rule-driven recurring instance (any frequency except "Once per
    # Period"). `due_date` is derived from this: occurrence_date +
    # due_days (see services/due_date_rules.py). Kept separate from
    # due_date so a template edit to `due_days` can recompute the deadline
    # without losing track of which occurrence this instance actually is,
    # and so "is this task's occurrence really in period X" checks (sync,
    # carry-forward, placeholder pruning) never have to guess from a
    # due_date that may now fall outside that period.
    occurrence_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # The N used to compute due_date from occurrence_date (due_date =
    # occurrence_date + due_days) — copied from the template/rule at
    # generation time, purely so a read path can show "N days after
    # landing" without re-deriving it. 0 for a non-recurring task.
    due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Optional time-of-day component, copied from the source TaskTemplate's
    # `due_time` at generation time (or set directly on a custom task).
    due_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    # Label of the previous period this same recurring task was carried
    # forward from while still incomplete (set at period close). NULL once
    # the task is actually completed in a period.
    carried_from_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Set by the Lead/Admin at assignment time — informational, doesn't
    # gate any workflow, purely for triage/sorting on the task board.
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, name="priority"), default=Priority.MEDIUM, nullable=False
    )
    # Optional freeform labels set by whoever assigns the task (Lead/Admin)
    # — purely for triage/search, not workflow-gating. Filtered via
    # ?tag= on the task board and GET /tasks; the GIN index above keeps
    # containment lookups fast.
    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default="{}", nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status"), default=TaskStatus.NOT_STARTED
    )
    rejection_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_time_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    submitted_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The instant this task actually reached COMPLETED. Kept in lock-step
    # with `status` by the `_stamp_completed_at` listener below (set on the
    # way in, cleared if the task is re-opened). `completed_at.date() >
    # due_date` ⇒ the work landed late — the UI shows "N days late".
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    plan: Mapped["Plan"] = relationship(back_populates="tasks")
    sub_tasks: Mapped[List["SubTask"]] = relationship(
        back_populates="task", cascade="all,delete-orphan"
    )
    entity_assignees: Mapped[List["TaskEntityAssignee"]] = relationship(
        back_populates="task",
        cascade="all,delete-orphan",
        order_by="TaskEntityAssignee.created_at",
    )


class TaskEntityAssignee(Base):
    """
    Whole-entity task assignment: one row per TEAM_MEMBER the task was
    fanned out to when a Lead used POST /tasks/{id}/assign-entity. Every
    row must have `done=True` before the task auto-submits for the Lead's
    review — a multi-contributor pattern requiring unanimous completion
    rather than a single assignee.
    """

    __tablename__ = "task_entity_assignees"
    __table_args__ = (
        Index("ix_task_entity_assignees_tenant", "tenant_id"),
        Index("ix_task_entity_assignees_task", "task_id"),
        Index("ix_task_entity_assignees_user", "user_id"),
        UniqueConstraint("task_id", "user_id", name="uq_task_entity_assignee_user"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional per-assignee deadline — lets the assigner stagger due dates
    # across the people sharing this task (e.g. one person's part feeds
    # into another's) while the task itself keeps a single overall due
    # date. Always <= the task's own due_date (enforced where it's set,
    # in routers/tasks.py); purely informational otherwise — it doesn't
    # drive `done`/status on its own.
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Required accountability details captured when THIS person marks
    # their own row done — mirrors Task/SubTask.completion_notes /
    # completion_time_minutes, just per-assignee instead of per-task.
    completion_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_time_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task: Mapped["Task"] = relationship(back_populates="entity_assignees")


class SubTask(Base):
    __tablename__ = "sub_tasks"
    __table_args__ = (
        Index("ix_subtasks_tenant", "tenant_id"),
        Index("ix_subtasks_task", "task_id"),
        Index("ix_subtasks_assigned", "assigned_to_id"),
        Index("ix_subtasks_reviewer", "reviewer_id"),
        Index("ix_subtasks_tenant_status", "tenant_id", "status"),
        Index("ix_subtasks_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    source_template_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("sub_task_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_to_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Same WITHIN_PERIOD/AFTER_PERIOD_END window as Task.due_date_type, but
    # always additionally capped at the parent task's own due_date — see
    # services/due_date_rules.py.
    due_date_type: Mapped[DueDateType] = mapped_column(
        SAEnum(DueDateType, name="due_date_type"),
        default=DueDateType.WITHIN_PERIOD,
        server_default="WITHIN_PERIOD",
        nullable=False,
    )
    post_period_due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Same purpose as Task.occurrence_date — the raw date THIS recurring
    # sub-task instance landed on (only set when the sub-task itself has
    # its own frequency); due_date = occurrence_date + due_days, capped at
    # the parent task's own due_date.
    occurrence_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    due_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    carried_from_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Set by the Lead/Admin at assignment time — informational, doesn't
    # gate any workflow, purely for triage/sorting on the task board.
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, name="priority"), default=Priority.MEDIUM, nullable=False
    )
    # Same freeform-label field as Task.tags — see the comment there.
    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default="{}", nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status"), default=TaskStatus.NOT_STARTED
    )
    rejection_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_time_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    submitted_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # See Task.completed_at — same contract, one level down.
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Whole-entity / hand-picked multi-assignee mode — mirrors
    # Task.assigned_entity_wide. When true, `assigned_to_id` is cleared and
    # the work is fanned out to SubTaskEntityAssignee rows; the sub-task
    # only auto-submits for review once every row is done.
    assigned_entity_wide: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    task: Mapped["Task"] = relationship(back_populates="sub_tasks")
    entity_assignees: Mapped[List["SubTaskEntityAssignee"]] = relationship(
        back_populates="subtask",
        cascade="all,delete-orphan",
        order_by="SubTaskEntityAssignee.created_at",
    )


def _stamp_completed_at(target, value, oldvalue, initiator):
    """Keep `completed_at` in lock-step with a COMPLETED status wherever the
    transition is made — router submit/approve, roll-up from sub-tasks, or
    a multi-assignee fan-in. Stamped once on the way in; cleared if the
    item is ever re-opened (un-approve / reject / carry-forward)."""
    if value == oldvalue:
        return
    if value == TaskStatus.COMPLETED:
        if getattr(target, "completed_at", None) is None:
            target.completed_at = datetime.now(timezone.utc)
    elif oldvalue == TaskStatus.COMPLETED:
        target.completed_at = None


event.listen(Task.status, "set", _stamp_completed_at)
event.listen(SubTask.status, "set", _stamp_completed_at)


class SubTaskEntityAssignee(Base):
    """One Team Member's completion row on a multi-assignee sub-task —
    exact mirror of TaskEntityAssignee, one level down. Every row must be
    `done=True` before the sub-task auto-submits for the Lead's review."""

    __tablename__ = "sub_task_entity_assignees"
    __table_args__ = (
        Index("ix_sub_task_entity_assignees_tenant", "tenant_id"),
        Index("ix_sub_task_entity_assignees_subtask", "subtask_id"),
        Index("ix_sub_task_entity_assignees_user", "user_id"),
        UniqueConstraint(
            "subtask_id", "user_id", name="uq_sub_task_entity_assignee_user"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subtask_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sub_tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional per-assignee deadline — see TaskEntityAssignee.due_date;
    # always <= this sub-task's own due_date (enforced where it's set).
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completion_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completion_time_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    subtask: Mapped["SubTask"] = relationship(back_populates="entity_assignees")


# ============================================================
# Carry-forward
# ============================================================
class CarryForwardGroup(Base):
    """A batch of items proposed to roll into the next period when current closes."""

    __tablename__ = "carry_forward_groups"
    __table_args__ = (
        Index("ix_cfg_tenant", "tenant_id"),
        Index("ix_cfg_from_period", "from_period_id"),
        Index("ix_cfg_to_period", "to_period_id"),
        Index("ix_cfg_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    from_period_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("periods.id", ondelete="CASCADE"), nullable=False
    )
    to_period_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("periods.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[CarryForwardStatus] = mapped_column(
        SAEnum(CarryForwardStatus, name="carry_forward_status"),
        default=CarryForwardStatus.PENDING,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    items: Mapped[List["CarryForwardItem"]] = relationship(
        back_populates="group", cascade="all,delete-orphan"
    )


class CarryForwardItem(Base):
    __tablename__ = "carry_forward_items"
    __table_args__ = (
        Index("ix_cfi_group", "group_id"),
        Index("ix_cfi_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("carry_forward_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_plan_id: Mapped[str] = mapped_column(String(64), nullable=False)
    original_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    original_subtask_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    original_assignee_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    original_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)  # TASK | SUBTASK
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    copied_to_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    copied_to_subtask_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    group: Mapped["CarryForwardGroup"] = relationship(back_populates="items")


# ============================================================
# Audit log (append-only)
# ============================================================
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_tenant", "tenant_id"),
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ============================================================
# Notification (kept simple — used by services to surface alerts)
# ============================================================
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user", "tenant_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ============================================================
# Time tracking (drives reports 2.3 and 2.5)
# ============================================================
class TimeEntry(Base):
    """
    A single block of time a user logged against a work item.

    Bound to ONE of (task | subtask) for granular reporting,
    but always carries an `entity_id` (denormalized from the parent) so
    the Time Entry Report can roll up by entity without extra joins.
    `duration_minutes` is authoritative — `started_at` / `ended_at` are
    optional (for live-timer flows that may be added later).
    """

    __tablename__ = "time_entries"
    __table_args__ = (
        Index("ix_time_entries_tenant", "tenant_id"),
        Index("ix_time_entries_user", "user_id"),
        Index("ix_time_entries_entity", "entity_id"),
        Index("ix_time_entries_task", "task_id"),
        Index("ix_time_entries_subtask", "subtask_id"),
        Index("ix_time_entries_tenant_date", "tenant_id", "entry_date"),
        Index("ix_time_entries_tenant_user_date", "tenant_id", "user_id", "entry_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    subtask_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("sub_tasks.id", ondelete="SET NULL"), nullable=True
    )
    # Convenience period reference for fast period-range filtering in reports.
    # Nullable because a manually-entered time block does not have to belong
    # to a specific period.
    period_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("periods.id", ondelete="SET NULL"), nullable=True
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ============================================================
# Comments (task detail side-panel discussion thread)
# ============================================================
class Comment(Base):
    """A single comment on a Task. Visibility mirrors GET /api/tasks/{id} —
    anyone who can view the task can read and post comments on it.
    Immutable once posted except for deletion (by the author or an admin);
    there's no edit endpoint, matching the simple "discussion thread"
    the task detail panel needs."""

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_tenant", "tenant_id"),
        Index("ix_comments_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Attachment(Base):
    """A file attached to a Task or a SubTask — uploaded by whoever is
    assigning the work (Lead/Admin) or by the Team Member executing it
    (visibility mirrors Comment: anyone who can view the task/sub-task
    can upload/read; only the uploader or an admin can delete). Exactly
    one of task_id / subtask_id is set — a task-level attachment when
    the task itself is the assignment unit, a sub-task-level one once
    the task has been broken into sub-tasks. The file bytes live on
    disk under UPLOAD_DIR (see services/attachment_storage.py); this
    row is metadata + the access-control anchor."""

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_tenant", "tenant_id"),
        Index("ix_attachments_task", "task_id"),
        Index("ix_attachments_subtask", "subtask_id"),
        CheckConstraint(
            "(task_id IS NOT NULL) <> (subtask_id IS NOT NULL)",
            name="ck_attachments_one_parent",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    subtask_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("sub_tasks.id", ondelete="CASCADE"), nullable=True
    )
    uploaded_by_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ============================================================
# Report snapshots (immutable audit evidence)
# ============================================================
class ReportSnapshot(Base):
    """
    Immutable snapshot of a generated report.

    Persisted whenever the Report Engine generates a report — either on
    demand by a user OR automatically (e.g. when an Admin closes a
    period, the Period Close Completion Report is snapshotted so Super
    Admins and Team Leads can pull it later for audit evidence).
    """

    __tablename__ = "report_snapshots"
    __table_args__ = (
        Index("ix_report_snapshots_tenant", "tenant_id"),
        Index("ix_report_snapshots_tenant_type", "tenant_id", "report_type"),
        Index("ix_report_snapshots_period", "period_id"),
        Index("ix_report_snapshots_tenant_generated", "tenant_id", "generated_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    report_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Optional context references — set when applicable.
    period_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("periods.id", ondelete="SET NULL"), nullable=True
    )
    entity_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True
    )
    target_user_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    generated_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # MANUAL | PERIOD_CLOSE_EVENT | SCHEDULED  — surfaces *why* this snapshot exists.
    trigger: Mapped[str] = mapped_column(String(40), nullable=False, default="MANUAL")
    parameters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )



class Holiday(Base):
    """A day the platform is closed (a public or bank holiday), on top of the
    Saturday/Sunday weekend. GLOBAL — deliberately not tenant-scoped, like the
    weekend rule itself: the date helpers that roll a due date off a
    non-working day are pure functions with no tenant in sight.

    The rows are read into the in-process registry (calendar/holiday_registry)
    by services/holiday_service, and from there every due-date roll, working-day
    count, recurrence and date picker follows them. `source` says who entered
    the row: MANUAL (the Platform Owner's Holidays page, POST /api/holidays, or
    the seeded 2083 BS list) or API (a bulk load or a sync from an external
    holiday feed). A feed never overwrites a MANUAL row.

    Several holidays may share a date (unique per date + name); they are one
    non-working day, never a double shift. `category` is ALL, or GENDER /
    RELIGION for one group (`audience`, e.g. FEMALE / CHRISTIAN); every
    recorded holiday is skipped for task dates whatever its category (the rule
    is calendar.holiday_registry.SKIPPED_FOR_EVERYONE). `holiday_date` (AD) is
    the source of truth; `bs_date` is the same day in Bikram Sambat,
    "YYYY-MM-DD"."""

    __tablename__ = "holidays"
    __table_args__ = (
        UniqueConstraint("holiday_date", "name", name="uq_holidays_date_name"),
        CheckConstraint(
            "category IN ('ALL', 'GENDER', 'RELIGION')", name="ck_holidays_category"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    bs_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ALL", server_default="ALL"
    )
    audience: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ============================================================
# Automated HRMS timesheet
# ============================================================
class HrmsActivityMapping(Base):
    """A task -> HRMS activity choice somebody confirmed, remembered for the
    next month. `key_kind` is "template" (the task/sub-task template id, exact)
    or "title" (the normalised title, for custom tasks). `hrms_sub_activity_id`
    0 means "no sub activity"."""

    __tablename__ = "hrms_activity_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "hrms_project_id",
            "key_kind",
            "key_value",
            name="uq_hrms_activity_mapping",
        ),
        CheckConstraint("key_kind IN ('template', 'title')", name="ck_hrms_map_kind"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    hrms_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    key_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    key_value: Mapped[str] = mapped_column(String(300), nullable=False)
    hrms_activity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    hrms_sub_activity_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    confirmed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    confirmed_by_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TimesheetSubmission(Base):
    """One "Submit to HRMS" action by one user for one project and date range.
    `idempotency_key` makes a double click / retried request return this row
    instead of sending the entries again."""

    __tablename__ = "timesheet_submissions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "idempotency_key", name="uq_timesheet_idem"
        ),
        Index("ix_timesheet_submissions_user", "tenant_id", "user_id", "range_start"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    hrms_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    hrms_project_name: Mapped[str] = mapped_column(String(300), nullable=False)
    # The HRMS identifies the employee by e-mail (what is sent). The id is only
    # recorded when the project's member list happened to show one.
    hrms_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    hrms_employee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    # SUBMITTING | SUBMITTED | PARTIAL | FAILED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SUBMITTING"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lines: Mapped[List["TimesheetLine"]] = relationship(
        back_populates="submission",
        cascade="all,delete-orphan",
        order_by="TimesheetLine.work_date",
    )


class TimesheetLine(Base):
    """One HRMS timesheet entry (date + activity [+ sub activity]) built from
    one or more time entries. `minutes` is the truth; `hours` is what is sent."""

    __tablename__ = "timesheet_lines"
    __table_args__ = (
        Index("ix_timesheet_lines_submission", "submission_id"),
        # A day's entry for an activity can reach the HRMS once. The slot is
        # claimed while the line is still PENDING - before anything is sent - so
        # two simultaneous submissions can't both go out; a FAILED attempt
        # gives the slot back and can be redone.
        Index(
            "uq_timesheet_lines_live",
            "tenant_id",
            "user_id",
            "hrms_project_id",
            "work_date",
            "hrms_activity_id",
            "hrms_sub_activity_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'SENT', 'UNCERTAIN')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    submission_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("timesheet_submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    hrms_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    hrms_activity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Unused since sub activities became fields to fill (always 0); it stays only
    # because it is part of the unique index below. The values live in `sub_values`.
    hrms_sub_activity_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # {sub activity id: value} sent with the activity (JSON keys are strings).
    sub_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    hours: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    work_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="BILLABLE", server_default="BILLABLE"
    )
    match_tier: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    match_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    match_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # PENDING (about to send) | SENT | FAILED (definitely refused) |
    # UNCERTAIN (no answer - may or may not have arrived; verify before retry)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    hrms_entry_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    submission: Mapped["TimesheetSubmission"] = relationship(back_populates="lines")
    sources: Mapped[List["TimesheetLineSource"]] = relationship(
        back_populates="line", cascade="all,delete-orphan"
    )


class TimesheetLineSource(Base):
    """A time entry that fed a timesheet line. `time_entry_id` is UNIQUE, so one
    time entry can be part of one live line only - the database-level guard
    against counting the same minutes twice. `minutes` is a snapshot, so a later
    edit of the time entry is noticed rather than silently re-sent."""

    __tablename__ = "timesheet_line_sources"
    __table_args__ = (
        UniqueConstraint("time_entry_id", name="uq_timesheet_source_entry"),   # legacy (time-log based) rows
        # A task / sub task someone SUBMITTED is filed once PER PERSON (two people can each
        # file their own part of a group task): the database rule behind "never twice".
        UniqueConstraint("user_id", "task_id", name="uq_timesheet_source_user_task"),
        UniqueConstraint("user_id", "subtask_id", name="uq_timesheet_source_user_subtask"),
        Index("ix_timesheet_sources_line", "line_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    line_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("timesheet_lines.id", ondelete="CASCADE"), nullable=False
    )
    # Whose submission this is (the line's owner).
    user_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # Only used by rows written before timesheets stopped reading the time-log table.
    time_entry_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("time_entries.id", ondelete="SET NULL"), nullable=True
    )
    # The task OR sub task whose submission this row files.
    task_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    subtask_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("sub_tasks.id", ondelete="SET NULL"), nullable=True
    )
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    line: Mapped["TimesheetLine"] = relationship(back_populates="sources")


class TimesheetReminderLog(Base):
    """A timesheet reminder already sent, so a scheduler restart or a re-run
    never sends the same one twice."""

    __tablename__ = "timesheet_reminder_log"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "calendar",
            "period_key",
            "stage",
            "sent_on",
            name="uq_timesheet_reminder",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    calendar: Mapped[str] = mapped_column(String(2), nullable=False)  # AD | BS
    period_key: Mapped[str] = mapped_column(String(10), nullable=False)  # 2026-10
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    sent_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
