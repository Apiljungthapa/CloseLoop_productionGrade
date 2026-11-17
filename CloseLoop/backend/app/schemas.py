"""Pydantic schemas — all request/response shapes for CloseLoop PHASE 1."""

from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional, List, Any, Dict, Literal

from pydantic import (
    BaseModel,
    EmailStr,
    ConfigDict,
    field_validator,
    model_validator,
    Field,
    computed_field,
)

from .calendar import bs_iso, holiday_skips_everyone
from .models import (
    Role,
    TenantStatus,
    EntityGroupType,
    PeriodStatus,
    PeriodCadence,
    CalendarType,
    PlanStatus,
    TaskStatus,
    RecurrenceFrequency,
    CarryForwardStatus,
    DueAnchor,
    DueDateType,
    Priority,
)


# ============================================================
# Auth
# ============================================================
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MeUpdateIn(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


# ============================================================
# Tenants
# ============================================================
class SuperAdminInline(BaseModel):
    name: str
    email: EmailStr
    password: str


class TenantIn(BaseModel):
    name: str
    # URL slug (the .../<slug>/dashboard segment). Optional on create — left
    # blank, it's auto-generated from `name`; on update, blank leaves the
    # existing slug untouched (send the current value back to change it).
    slug: Optional[str] = None
    status: TenantStatus = TenantStatus.active
    plan_limit: int = 1000
    super_admin: Optional[SuperAdminInline] = None


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    slug: str
    status: TenantStatus
    plan_limit: int
    client_id: Optional[str] = None
    created_at: datetime


class TenantCredentialsOut(BaseModel):
    """Returned only at issuance (tenant creation) and rotation — the one
    and only time the raw secret is ever available. Never returned by any
    GET endpoint afterward."""

    tenant_id: str
    client_id: str
    client_secret: str


class TenantCreatedOut(TenantOut):
    """TenantOut plus the one-time client_secret, returned only from the
    POST /tenants creation response."""

    client_secret: str


# ============================================================
# OAuth2 client-credentials (machine-to-machine API access)
# ============================================================
class OAuthTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str = "integration"


# ============================================================
# Integration API (client-credentials-only, read-only) response shapes.
# Deliberately lightweight/flat — no nested task trees or cross-user
# aggregates — since a machine client has no per-user visibility rules to
# apply; it either sees this tenant's data at this level or it doesn't.
# ============================================================
class IntegrationPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    label: str
    start_date: date
    end_date: date
    status: PeriodStatus


class IntegrationPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    period_id: str
    entity_id: str
    title: str
    status: PlanStatus
    due_date: Optional[date] = None
    created_at: datetime


# ============================================================
# Users
# ============================================================
class UserIn(BaseModel):
    email: EmailStr
    name: str
    role: Role
    password: str
    title: Optional[str] = None
    primary_entity_id: Optional[str] = None
    tenant_id: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[Role] = None
    title: Optional[str] = None
    primary_entity_id: Optional[str] = None
    password: Optional[str] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    name: str
    role: Role
    title: Optional[str] = None
    tenant_id: Optional[str]
    # The frontend's org-scoped routes (.../<slug>/dashboard) key off this —
    # None for a Platform Owner, who isn't scoped to any tenant.
    tenant_slug: Optional[str] = None
    primary_entity_id: Optional[str]
    created_at: datetime
    last_login: Optional[datetime] = None


class LoginOut(UserOut):
    """Login/refresh response — adds the bearer token on top of UserOut so
    it can be copy-pasted into Swagger's "Authorize" dialog (or any non-
    browser API client) without needing to inspect the httpOnly cookie.
    The web app itself ignores this field and relies on the cookie."""

    access_token: str


class UserMini(BaseModel):
    """Lightweight user reference for embedding in other responses."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    title: Optional[str] = None


# Assignments
class UserEntityAssignIn(BaseModel):
    user_id: str
    entity_id: str


class UserEntityAssignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    entity_id: str
    assigned_at: datetime
    # enriched
    entity_name: Optional[str] = None


class UserGroupAssignIn(BaseModel):
    user_id: str
    group_id: str


class UserGroupAssignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    group_id: str
    assigned_at: datetime
    # enriched
    group_name: Optional[str] = None


class UserEntityAssignBulkIn(BaseModel):
    """Bulk-assign secondary entities to a user.

    mode: 'add' appends, 'replace' overwrites, 'remove' unassigns the listed entities.
    """

    user_id: str
    entity_ids: List[str]
    mode: str = "add"  # add | replace | remove


class UserGroupAssignBulkIn(BaseModel):
    user_id: str
    group_ids: List[str]
    mode: str = "add"  # add | replace | remove


class EntityMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class EntityGroupMini(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class UserAssignmentsOut(BaseModel):
    """Combined view: primary entity + secondary entities + group assignments."""

    user_id: str
    primary_entity: Optional[EntityMini] = None
    secondary_entities: List[EntityMini] = []
    groups: List[EntityGroupMini] = []


# ============================================================
# Entity groups + entities
# ============================================================
class EntityGroupIn(BaseModel):
    name: str
    type: EntityGroupType = EntityGroupType.CUSTOM
    parent_id: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


class EntityGroupUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[EntityGroupType] = None
    parent_id: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class EntityGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    parent_id: Optional[str]
    name: str
    type: EntityGroupType
    description: Optional[str]
    is_active: bool = True
    created_at: datetime
    # enriched (populated by router when available)
    entity_count: int = 0


class EntityGroupTreeOut(EntityGroupOut):
    children: List["EntityGroupTreeOut"] = []
    entities: List["EntityOut"] = []


class EntityIn(BaseModel):
    name: str
    group_ids: List[str]
    primary_group_id: Optional[str] = None
    description: Optional[str] = None


class EntityUpdate(BaseModel):
    name: Optional[str] = None
    group_ids: Optional[List[str]] = None
    primary_group_id: Optional[str] = None
    description: Optional[str] = None


class EntityBulkAssignIn(BaseModel):
    """Bulk attach/detach groups for many entities at once.

    mode: 'add' appends to existing memberships, 'replace' overwrites the full set,
    'remove' detaches the given groups (primary group cannot be removed).
    """

    entity_ids: List[str]
    group_ids: List[str]
    mode: str = "add"  # add | replace | remove


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    group_id: str
    group_ids: List[str] = Field(default_factory=list)
    name: str
    description: Optional[str]
    created_at: datetime
    # enriched
    primary_group_name: Optional[str] = None
    group_names: List[str] = Field(default_factory=list)


# ============================================================
# Period configuration + periods
# ============================================================
class PeriodConfigIn(BaseModel):
    cadence: PeriodCadence = PeriodCadence.FIXED_WEEKS
    start_day_of_week: int = 4  # Fri — only used when cadence == FIXED_WEEKS
    duration_weeks: int = 4  # only used when cadence == FIXED_WEEKS
    auto_generate: bool = True
    carry_forward_enabled: bool = True

    @field_validator("start_day_of_week")
    @classmethod
    def _valid_dow(cls, v: int) -> int:
        if v < 0 or v > 6:
            raise ValueError("start_day_of_week must be 0..6 (Mon..Sun)")
        return v

    @field_validator("duration_weeks")
    @classmethod
    def _valid_weeks(cls, v: int) -> int:
        if v < 1 or v > 52:
            raise ValueError("duration_weeks must be 1..52")
        return v


class PeriodConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    cadence: PeriodCadence
    start_day_of_week: int
    duration_weeks: int
    auto_generate: bool
    carry_forward_enabled: bool


class PeriodGenerateIn(BaseModel):
    """Generate a whole schedule of periods matching one cadence, starting
    at `start_date` — the universal alternative to hand-pasting rows or the
    one-off 2026 retail-calendar seed. Replaces the tenant's existing
    periods (same semantics as /periods/import)."""

    cadence: PeriodCadence = PeriodCadence.FIXED_WEEKS
    start_date: date
    period_count: int = 12
    # Only used when cadence == FIXED_WEEKS; defaults from the tenant's
    # saved PeriodConfiguration when omitted.
    duration_weeks: Optional[int] = None
    # Only used when cadence == FIXED_WEEKS; defaults from the tenant's
    # saved PeriodConfiguration when omitted. `start_date` is snapped
    # forward to the next occurrence of this weekday before generating —
    # without it, period 1 started on whatever raw date was submitted
    # (the UI defaults that to today) instead of the tenant's configured
    # fixed-week day, so it could land on a different weekday than every
    # period after it.
    start_day_of_week: Optional[int] = None

    @field_validator("period_count")
    @classmethod
    def _valid_count(cls, v: int) -> int:
        if v < 1 or v > 104:
            raise ValueError("period_count must be 1..104")
        return v

    @field_validator("duration_weeks")
    @classmethod
    def _valid_duration(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 52):
            raise ValueError("duration_weeks must be 1..52")
        return v

    @field_validator("start_day_of_week")
    @classmethod
    def _valid_dow(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 6):
            raise ValueError("start_day_of_week must be 0..6 (Mon..Sun)")
        return v


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    label: str
    start_date: date
    end_date: date
    status: PeriodStatus
    closed_at: Optional[datetime]
    next_period_id: Optional[str]
    created_at: datetime
    # Bikram Sambat equivalents of start_date/end_date — Period itself only
    # stores AD dates; these are derived at read time (see periods.py) for
    # the BS-aware period table and calendar widget in the UI.
    bs_start_label: Optional[str] = None
    bs_end_label: Optional[str] = None
    bs_start_iso: Optional[str] = None
    bs_end_iso: Optional[str] = None


class PeriodSeedIn(BaseModel):
    label: str
    start_date: date
    end_date: date


class PeriodCloseOut(BaseModel):
    closed_period: PeriodOut
    next_period: PeriodOut
    plans_generated: int
    carry_forward_items: int


class PeriodPreviewIn(BaseModel):
    """Dry-run of /periods/generate — same inputs, but returns the seeds
    that WOULD be created (with BS labels + working-day/closing-date/holiday
    detail) without touching the database. Powers the "preview before you
    commit" step in the period-generation UI."""

    cadence: PeriodCadence = PeriodCadence.FIXED_WEEKS
    start_date: date
    period_count: int = 12
    duration_weeks: Optional[int] = None
    start_day_of_week: Optional[int] = None

    @field_validator("period_count")
    @classmethod
    def _preview_valid_count(cls, v: int) -> int:
        if v < 1 or v > 104:
            raise ValueError("period_count must be between 1 and 104")
        return v

    @field_validator("start_day_of_week")
    @classmethod
    def _preview_valid_dow(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 6):
            raise ValueError("start_day_of_week must be 0..6 (Mon..Sun)")
        return v


class PeriodPreviewSeedOut(BaseModel):
    label: str
    start_date: date
    end_date: date
    bs_start_label: str
    bs_end_label: str
    total_days: int
    working_days: int
    closing_date: date
    holidays: List[dict] = Field(default_factory=list)


class PeriodPreviewOut(BaseModel):
    cadence: PeriodCadence
    periods: List[PeriodPreviewSeedOut]


# ---- Nepali (BS) calendar --------------------------------------------------


class CalendarDayOut(BaseModel):
    ad_date: date
    bs_year: int
    bs_month: int
    bs_day: int
    bs_month_name: str
    weekday: int  # AD convention: 0=Mon .. 6=Sun
    is_weekend: bool
    # Weekend or a CONFIGURED holiday — a day no task may be due on. `holiday`
    # below also carries the library's estimated festivals, which only label
    # the day.
    is_non_working: bool = False
    is_today: bool
    holiday: Optional[str] = None
    period_id: Optional[str] = None
    period_label: Optional[str] = None
    period_status: Optional[str] = None
    period_start_date: Optional[date] = None
    period_end_date: Optional[date] = None


class CalendarMonthOut(BaseModel):
    ad_year: int
    ad_month: int
    ad_month_label: str
    leading_blanks: int  # empty cells before day 1 in a Sun-first grid
    days: List[CalendarDayOut]


class FiscalYearMonthOut(BaseModel):
    bs_year: int
    bs_month: int
    label: str
    ad_start: date
    ad_end: date


class FiscalYearOut(BaseModel):
    start_year: int
    label: str
    bs_start_label: str
    bs_end_label: str
    ad_start: date
    ad_end: date
    months: List[FiscalYearMonthOut]
    quarters: List[List[FiscalYearMonthOut]]
    halves: List[List[FiscalYearMonthOut]]


class TodayOut(BaseModel):
    ad_date: date
    bs_label: str
    bs_year: int
    bs_month: int
    bs_day: int
    fiscal_year_label: str
    fiscal_year_start: int


class BsConvertOut(BaseModel):
    """One AD date rendered on the Bikram Sambat calendar — powers the BS
    date-picker's closed-field label."""

    ad_date: date
    bs_year: int
    bs_month: int
    bs_day: int
    bs_month_name: str
    bs_label: str   # e.g. "Shrawan 1, 2082"
    bs_iso: str     # e.g. "2082-04-01"


class BsConvertBatchIn(BaseModel):
    """Many AD dates to render on the BS calendar in one call — e.g. every
    due date shown across a BS-mode pack's task table, detail panel and
    assignment sections, without one request per date."""

    dates: List[date]

    @field_validator("dates")
    @classmethod
    def _cap_size(cls, v: List[date]) -> List[date]:
        if len(v) > 500:
            raise ValueError("At most 500 dates per batch")
        return v


class BsConvertBatchOut(BaseModel):
    """{ 'YYYY-MM-DD' (the AD date) -> bs_label }, one entry per date in
    BsConvertBatchIn.dates — bs_label uses the exact same format as
    BsConvertOut.bs_label, so it matches what the date-picker's own closed
    field already shows for that date."""

    labels: Dict[str, str]


class BsMonthDayOut(BaseModel):
    bs_day: int
    ad_date: date
    weekday: int          # AD convention: 0=Mon .. 6=Sun
    is_weekend: bool      # Saturday + Sunday (calendar.NEPAL_WEEKEND)
    is_non_working: bool = False  # weekend or a configured holiday
    is_today: bool
    holiday: Optional[str] = None


class BsMonthOut(BaseModel):
    """A whole Bikram Sambat month, laid out for a Sun-first picker grid.
    Navigation is by BS month (prev/next), unlike /calendar/month which
    walks AD months."""

    bs_year: int
    bs_month: int
    bs_month_name: str
    ad_range_label: str   # e.g. "17 Jul – 16 Aug 2025"
    leading_blanks: int   # empty cells before day 1 in a Sun-first grid
    days: List[BsMonthDayOut]
    prev_bs_year: int
    prev_bs_month: int
    next_bs_year: int
    next_bs_month: int


# ---- Holidays --------------------------------------------------------------


class HolidayIn(BaseModel):
    """One holiday. The AD date is the source of truth; `bs_date` is optional
    and, when sent, must be the same day in Bikram Sambat (it is filled in
    from the AD date otherwise). `category` ALL is for everyone; GENDER /
    RELIGION name one group and need `audience` (who: e.g. Female, Christian).
    Every recorded holiday is skipped for task dates (`HolidayOut.non_working`)."""

    holiday_date: date
    name: str = Field(max_length=200)
    bs_date: Optional[str] = None
    category: Literal["ALL", "GENDER", "RELIGION"] = "ALL"
    audience: Optional[str] = Field(default=None, max_length=50)
    remarks: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A holiday needs a name")
        return v

    @field_validator("holiday_date")
    @classmethod
    def _plausible_year(cls, v: date) -> date:
        if not 2000 <= v.year <= 2100:
            raise ValueError("Holiday date must be between the years 2000 and 2100")
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _category_any_case(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("audience", "remarks", "bs_date")
    @classmethod
    def _blank_is_none(cls, v: Optional[str]) -> Optional[str]:
        v = v.strip() if isinstance(v, str) else v
        return v or None

    @model_validator(mode="after")
    def _consistent(self) -> "HolidayIn":
        if self.category == "ALL":
            self.audience = None
        elif not self.audience:
            raise ValueError(
                "A gender or religion holiday needs 'applies to' (for example Female or Christian)"
            )
        else:
            self.audience = self.audience.upper()
        if self.bs_date:
            expected = bs_iso(self.holiday_date)
            if expected is not None and self.bs_date != expected:
                raise ValueError(
                    f"BS date {self.bs_date} does not match {self.holiday_date.isoformat()} "
                    f"(that day is {expected} BS)"
                )
        return self


class HolidayOut(BaseModel):
    id: str
    holiday_date: date
    bs_date: Optional[str] = None
    name: str
    category: str  # ALL | GENDER | RELIGION
    audience: Optional[str] = None
    remarks: Optional[str] = None
    source: str  # MANUAL | API

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def non_working(self) -> bool:
        """Is it a day off for everyone's task dates (skipped by due dates,
        recurrence and the pickers)? Decided once, server side, by
        calendar.holiday_registry.SKIPPED_FOR_EVERYONE."""
        return holiday_skips_everyone(self.category)


class HolidayBulkIn(BaseModel):
    """Upsert by date + name. `source` labels where the batch came from: a script
    loading a public-holiday feed sends API, which never overwrites a MANUAL
    row."""

    holidays: List[HolidayIn] = Field(min_length=1, max_length=2000)
    source: Literal["MANUAL", "API"] = "MANUAL"


class HolidayBulkOut(BaseModel):
    created: int
    updated: int
    skipped: int  # API rows that would have overwritten a MANUAL one


# ============================================================
# Close pack
# ============================================================
class ClosePackIn(BaseModel):
    name: str
    description: Optional[str] = None
    calendar_type: CalendarType = CalendarType.AD
    active: bool = True


class ClosePackUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    calendar_type: Optional[CalendarType] = None
    active: Optional[bool] = None


class ClosePackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    name: str
    description: Optional[str]
    calendar_type: CalendarType
    active: bool
    current_version: int
    created_at: datetime


# ----- Current-period report -----
class PackReportSubTaskRow(BaseModel):
    id: str
    title: str
    status: TaskStatus
    due_date: Optional[date] = None
    assigned: bool = False
    completion_notes: Optional[str] = None


class PackReportTaskRow(BaseModel):
    id: str
    title: str
    entity_id: str
    entity_name: Optional[str] = None
    status: TaskStatus
    due_date: Optional[date] = None
    assigned: bool = False
    assignee_name: Optional[str] = None
    completion_notes: Optional[str] = None
    subtasks: List[PackReportSubTaskRow] = []


class PackCurrentPeriodReport(BaseModel):
    pack_id: str
    pack_name: str
    calendar_type: CalendarType
    period: Optional[Dict[str, Any]] = None  # id / label / start_date / end_date / BS labels
    totals: Dict[str, int]
    completion_pct: float
    tasks: List[PackReportTaskRow] = []


class ClosePackVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    close_pack_id: str
    version_number: int
    snapshot_json: Dict[str, Any]
    created_at: datetime


# ----- Templates -----
class PostPeriodDueDaysMixin(BaseModel):
    """Shared `due_date_type` + `post_period_due_days` fields for anything
    that resolves/accepts a due date against a period — Close Pack task and
    sub-task templates, and Task/SubTask create/update payloads. See
    services/due_date_rules.py, the single place these two fields actually
    get interpreted.

    "N days after period end" is retired: a due date always stays inside its
    own period. Both fields are still accepted (old clients, bulk imports) but
    are coerced to WITHIN_PERIOD / 0 on the way in, so nothing can store it."""

    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0

    @model_validator(mode="before")
    @classmethod
    def _after_period_end_means_last_day(cls, data):
        # A template that asked for "N days after period end" ignored its own
        # day/anchor; now that it stays in the period it means "the period's
        # last working day" (day 1 counted back from the end), not day 1 from
        # the start.
        if isinstance(data, dict) and data.get("due_date_type") in (
            DueDateType.AFTER_PERIOD_END, DueDateType.AFTER_PERIOD_END.value,
        ):
            data = {**data, "due_anchor": DueAnchor.PERIOD_END, "due_day_of_period": 1}
        return data

    @field_validator("due_date_type")
    @classmethod
    def _retire_after_period_end(cls, v: DueDateType) -> DueDateType:
        return DueDateType.WITHIN_PERIOD

    @field_validator("post_period_due_days")
    @classmethod
    def _non_negative_post_period_days(cls, v: int) -> int:
        return 0


# A Close Pack's task/subtask DEFINITIONS. `due_day_of_period` is day N of
# whichever period a plan is generated for; a sub-task's day can never be
# after its parent task's (enforced in routers/close_packs.py).
class SubTaskTemplateIn(PostPeriodDueDaysMixin):
    title: str
    description: Optional[str] = None
    due_day_of_period: Optional[int] = None
    due_anchor: DueAnchor = DueAnchor.PERIOD_START
    due_time: Optional[time] = None
    # A sub-task can recur independently of its parent task — one sub-task
    # row per occurrence UNDER EACH parent task occurrence (e.g. a DAILY
    # sub-task under one MONTHLY task -> ~30 rows for that one task). NULL
    # = single sub-task (the default). Same shape as TaskTemplateIn's own
    # recurrence fields; ignored when the parent task itself is DAILY/
    # WEEKLY (see plan_generation_service._subtask_dates).
    frequency: Optional[RecurrenceFrequency] = None
    interval_count: int = 1
    days_of_week: Optional[List[int]] = None
    recurrence_start: Optional[date] = None
    recurrence_end: Optional[date] = None
    # N-Day Due Date for THIS sub-task's own recurrence — only meaningful
    # when `frequency` above is set. due_date = each occurrence's own
    # landing date + due_days (capped at the parent task's own due date),
    # never the period's end. Ignored when `frequency` is None (this
    # sub-task's single date instead comes from due_day_of_period/
    # due_anchor or due_date_type/post_period_due_days above).
    due_days: int = 0
    order: int = 0

    @field_validator("interval_count")
    @classmethod
    def _positive_interval(cls, v: int) -> int:
        return v if v and v >= 1 else 1

    @field_validator("due_days")
    @classmethod
    def _non_negative_due_days(cls, v: int) -> int:
        return v if v and v > 0 else 0

    @field_validator("recurrence_end")
    @classmethod
    def _end_after_start(cls, v, info):
        s = info.data.get("recurrence_start")
        if v and s and v < s:
            raise ValueError("recurrence_end can't be before recurrence_start")
        return v


class SubTaskTemplateOut(SubTaskTemplateIn):
    model_config = ConfigDict(from_attributes=True)
    id: str
    task_template_id: str


class TaskTemplateIn(PostPeriodDueDaysMixin):
    title: str
    description: Optional[str] = None
    # A task always has a concrete day (defaults to 1 = period start / last
    # day, per the anchor); null / 0 from the form collapses to 1.
    due_day_of_period: Optional[int] = 1
    # PERIOD_START: day N = period start + (N-1). PERIOD_END: day N =
    # period end - (N-1), i.e. "N days from close". Ignored for recurring.
    # (A legacy AFTER_PERIOD_END input is rewritten to PERIOD_END day 1 — the
    # period's last working day — by PostPeriodDueDaysMixin.)
    due_anchor: DueAnchor = DueAnchor.PERIOD_START

    @field_validator("due_day_of_period")
    @classmethod
    def _day_at_least_one(cls, v):
        return v if (v and v >= 1) else 1
    # NULL / omitted = once per period. DAILY / WEEKLY recur sub-period;
    # MONTHLY / QUARTERLY / SEMI_ANNUAL / ANNUAL recur period-level.
    frequency: Optional[RecurrenceFrequency] = None
    interval_count: int = 1              # "Custom" multiplier: every N days/weeks/months/quarters/...
    days_of_week: Optional[List[int]] = None  # 0=Mon..6=Sun, for WEEKLY
    # Required (default) vs Optional — ONLY settable on a DAILY-frequency
    # template right now. Optional means an uncompleted instance never
    # becomes overdue / counts against completion compliance; it's still
    # generated and visible. See Task.is_required for the full behavior.
    is_required: bool = True
    # Recurrence anchor — the FIRST occurrence date. Required when frequency
    # is set; defaults to the generation period's start otherwise.
    recurrence_start: Optional[date] = None
    recurrence_end: Optional[date] = None
    # N-Day Due Date — only meaningful when `frequency` above is set (every
    # cadence EXCEPT "Once per Period"): each generated occurrence's due
    # date = that occurrence's own landing date + due_days, recomputed
    # fresh per occurrence, never the period's end and never waiting for
    # the next occurrence. 0 = due on the occurrence date itself (the
    # default, and the only behavior recurring templates had before this
    # field existed). Ignored when `frequency` is None.
    due_days: int = 0
    due_time: Optional[time] = None
    reviewer_required: bool = False
    # Default tags/priority carried onto every Task generated from this
    # template — both optional.
    tags: List[str] = []
    priority: Priority = Priority.MEDIUM
    order: int = 0

    @field_validator("interval_count")
    @classmethod
    def _positive_interval(cls, v: int) -> int:
        return v if v and v >= 1 else 1

    @field_validator("due_days")
    @classmethod
    def _non_negative_due_days(cls, v: int) -> int:
        return v if v and v > 0 else 0

    @field_validator("is_required")
    @classmethod
    def _required_only_daily(cls, v: bool, info):
        if not v and info.data.get("frequency") != RecurrenceFrequency.DAILY:
            raise ValueError("Optional is only available for a Daily recurring task right now")
        return v

    @field_validator("recurrence_end")
    @classmethod
    def _end_after_start(cls, v, info):
        s = info.data.get("recurrence_start")
        if v and s and v < s:
            raise ValueError("recurrence_end can't be before recurrence_start")
        return v

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        return _normalize_tags(v)


class TaskTemplateOut(TaskTemplateIn):
    model_config = ConfigDict(from_attributes=True)
    id: str
    close_pack_id: str
    sub_task_templates: List[SubTaskTemplateOut] = []


class ClosePackTreeOut(ClosePackOut):
    task_templates: List[TaskTemplateOut] = []


# ============================================================
# Plans / Tasks / Subtasks
# ============================================================
class PlanGenerateIn(BaseModel):
    close_pack_id: str
    period_id: str
    entity_ids: List[str]
    # Optional Team-Lead owner stamped onto every generated Plan.
    # Admin only; when omitted, plans are generated without an explicit owner
    # and remain editable only by admins until /api/plans/{id}/owner is called.
    owner_id: Optional[str] = None


class PlanGenerateOut(BaseModel):
    plans_created: int
    plan_ids: List[str]


class PlanUpdate(BaseModel):
    """Structural edits to a Plan envelope (admin or Plan owner)."""

    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[date] = None


class PlanOwnerAssignIn(BaseModel):
    """Admin assigns / reassigns a Team Lead as Plan owner. Pass null to clear."""

    owner_id: Optional[str] = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    period_id: str
    entity_id: str
    owner_id: Optional[str] = None
    source_pack_id: Optional[str]
    source_pack_version: Optional[int]
    title: str
    description: Optional[str]
    status: PlanStatus
    due_date: Optional[date]
    created_at: datetime
    # enriched aggregates (populated by router)
    task_count: int = 0
    completed_count: int = 0
    pending_review_count: int = 0
    progress: float = 0.0
    calendar_type: Optional[CalendarType] = None
    period_label: Optional[str] = None
    period_start_date: Optional[date] = None
    period_end_date: Optional[date] = None
    entity_name: Optional[str] = None
    owner: Optional[UserMini] = None
    # Whether the CALLER may structurally edit this plan — true for an
    # Admin, or a Team Lead where owner_id == the caller's own id. False
    # means read-only: the caller can view the full task/subtask tree but
    # every mutating endpoint on it (add/edit/delete task or subtask,
    # assign, approve) will 403.
    can_edit: bool = False
    tasks: List["TaskOut"] = []


# ----- Period preview (no DB writes — see plan_generation_service.preview_tasks_for_period) -----
class PlanPreviewSubtaskOut(BaseModel):
    template_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    frequency: Optional[RecurrenceFrequency] = None
    # >1 is how the template editor's "Custom" cadence is stored — e.g.
    # frequency=MONTHLY + interval_count=2 is "every 2 months".
    interval_count: int = 1
    due_date: Optional[date] = None
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0
    # N-Day Due Date — the raw occurrence date this due_date was computed
    # from (due_date = occurrence_date + due_days). None for a non-
    # recurring row. See services/due_date_rules.apply_due_days.
    occurrence_date: Optional[date] = None
    due_days: int = 0


class PlanPreviewTaskOut(BaseModel):
    template_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    frequency: Optional[RecurrenceFrequency] = None
    interval_count: int = 1
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []
    # False for a recurring task with no occurrence in the previewed
    # period — `due_date` is then None and `next_due_date` names the next
    # occurrence instead (mirrors a real "Upcoming" placeholder Task).
    occurs_in_period: bool = True
    due_date: Optional[date] = None
    next_due_date: Optional[date] = None
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0
    occurrence_date: Optional[date] = None
    due_days: int = 0
    # True for a recurring task a Team Lead / Admin added by hand ("Add Task"
    # with a repeat) rather than one a Close Pack template produces.
    is_custom: bool = False
    subtasks: List[PlanPreviewSubtaskOut] = []


class PlanPreviewOut(BaseModel):
    # None for the "Ad-hoc tasks" card: recurring tasks a Lead added to a
    # plan that has no Close Pack behind it.
    pack_id: Optional[str] = None
    pack_title: str
    entity_id: str
    entity_name: Optional[str] = None
    owner: Optional[UserMini] = None
    period_id: str
    period_label: Optional[str] = None
    calendar_type: Optional[CalendarType] = None
    tasks: List[PlanPreviewTaskOut] = []


def _normalize_tags(tags: Optional[List[str]]) -> List[str]:
    """Trim, drop blanks, de-dupe case-insensitively (keeping first
    casing seen), cap length/count. Shared by Task and SubTask tag
    fields so `?tag=` filtering behaves the same everywhere."""
    if not tags:
        return []
    seen_lower: set[str] = set()
    cleaned: list[str] = []
    for t in tags:
        t = (t or "").strip()
        if not t:
            continue
        if len(t) > 40:
            raise ValueError("Each tag must be 40 characters or fewer")
        if t.lower() not in seen_lower:
            seen_lower.add(t.lower())
            cleaned.append(t)
    if len(cleaned) > 15:
        raise ValueError("A task/sub-task may carry at most 15 tags")
    return cleaned


class TaskSubtaskInline(PostPeriodDueDaysMixin):
    """One sub-task row from the 'Add Task' modal's optional Subtasks
    section — created together with the parent task in the same call.
    `assigned_to_id`/`due_date`/`priority` each fall back to the parent
    task's own value (from TaskCreate) when omitted, so filling in the
    top-level Assignee/Due Date/Priority once covers every subtask row
    that doesn't override them."""

    title: str
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    priority: Optional[Priority] = None
    tags: List[str] = []

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        return _normalize_tags(v)


class TaskCreate(PostPeriodDueDaysMixin):
    plan_id: str
    title: str
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    # The Team Member who executes this task directly — only takes
    # effect when `subtasks` is empty (a task with sub-tasks can't also
    # carry a direct member assignment; it becomes each subtask's
    # fallback assignee instead).
    member_assigned_to_id: Optional[str] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []
    # Optional inline sub-tasks — the 'Add Task' modal's "+ Add Subtask"
    # rows. Created atomically with the task in this same call.
    subtasks: List[TaskSubtaskInline] = []
    # Optional recurrence — makes this a RECURRING custom task: instances
    # are materialized across the plan's period and the periods after it,
    # same rules as a Close Pack template (see services/recurrence.py).
    # Future-period instances are UPCOMING (locked) until their period runs.
    frequency: Optional[RecurrenceFrequency] = None
    interval_count: int = 1
    days_of_week: Optional[List[int]] = None
    # Required (default) vs Optional — same rule/restriction as
    # TaskTemplateIn.is_required: only settable alongside a DAILY frequency.
    is_required: bool = True
    # Last allowed occurrence date — only meaningful alongside `frequency`.
    # None = repeats indefinitely (until manually stopped/deleted). Same
    # field as TaskTemplateIn.recurrence_end.
    recurrence_end: Optional[date] = None
    # N-Day Due Date — only meaningful alongside `frequency` (every cadence
    # except a one-off task): each occurrence's due date = that
    # occurrence's own landing date + due_days. 0 = due on the occurrence
    # date itself. See services/due_date_rules.py.
    due_days: int = 0
    # 2+ hand-picked Team Members to whole-group-assign a RECURRING task to
    # right at creation (every occurrence in the series gets whole-entity-
    # style assignment — each marks their own part done — and the pick is
    # saved into the series' recurrence rule so periods materialized later
    # inherit it too). Only meaningful alongside `frequency`; a non-recurring
    # task still uses POST /tasks/{id}/assign-multi after creation. Ignored
    # (like member_assigned_to_id) when `subtasks` is non-empty.
    member_ids: Optional[List[str]] = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        return _normalize_tags(v)

    @field_validator("interval_count")
    @classmethod
    def _pos_interval(cls, v: int) -> int:
        return v if v and v >= 1 else 1

    @field_validator("is_required")
    @classmethod
    def _required_only_daily(cls, v: bool, info):
        if not v and info.data.get("frequency") != RecurrenceFrequency.DAILY:
            raise ValueError("Optional is only available for a Daily recurring task right now")
        return v

    @field_validator("due_days")
    @classmethod
    def _non_negative_due_days(cls, v: int) -> int:
        return v if v and v > 0 else 0

    @field_validator("recurrence_end")
    @classmethod
    def _end_after_start(cls, v, info):
        s = info.data.get("due_date")
        if v and s and v < s:
            raise ValueError("recurrence_end can't be before the first occurrence's due date")
        return v

    @field_validator("member_ids")
    @classmethod
    def _dedupe_member_ids(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if not v:
            return None
        seen: List[str] = []
        for uid in v:
            if uid and uid not in seen:
                seen.append(uid)
        return seen or None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    # The Team Member who executes a LEAF task (no sub-tasks). Set by an
    # admin/lead only; ignored/rejected if the task has sub-tasks.
    member_assigned_to_id: Optional[str] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    # Omit either to leave it unchanged. See PostPeriodDueDaysMixin /
    # services/due_date_rules.py — WITHIN_PERIOD (default) requires
    # due_date <= the plan's period end. AFTER_PERIOD_END / post_period_due_days
    # are retired and coerced to WITHIN_PERIOD / 0 by the validators below.
    due_date_type: Optional[DueDateType] = None
    post_period_due_days: Optional[int] = None
    priority: Optional[Priority] = None
    status: Optional[TaskStatus] = None
    # Omit entirely to leave tags untouched; pass [] to clear them.
    tags: Optional[List[str]] = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return v if v is None else _normalize_tags(v)

    @field_validator("due_date_type")
    @classmethod
    def _retire_after_period_end(cls, v: Optional[DueDateType]) -> Optional[DueDateType]:
        return None if v is None else DueDateType.WITHIN_PERIOD

    @field_validator("post_period_due_days")
    @classmethod
    def _non_negative_post_period_days(cls, v: Optional[int]) -> Optional[int]:
        return None if v is None else 0


class TaskReviewIn(BaseModel):
    """Team-Lead approval decision for a leaf task (no sub-tasks)."""

    decision: str  # APPROVE | REJECT
    note: Optional[str] = None  # rejection reason (required for REJECT)


class TaskSubmitIn(BaseModel):
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None

    @field_validator("completion_time_minutes")
    @classmethod
    def validate_time(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 0:
            raise ValueError("completion_time_minutes must be non-negative")
        return value


class TaskMultiAssignIn(BaseModel):
    """Assign a leaf task to a hand-picked set of people (not necessarily
    the whole entity) — see POST /tasks/{id}/assign-multi.

    `due_dates` optionally gives one or more of those people their own
    deadline (e.g. person A's part feeds into person B's, so A gets an
    earlier date) — keyed by user_id, every key must also appear in
    `user_ids`. Each date is capped at the task's own due date by the
    endpoint (not checkable here — that needs the loaded Task)."""

    user_ids: List[str]
    due_dates: Optional[Dict[str, Optional[date]]] = None

    @field_validator("user_ids")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Select at least one person to assign this task to")
        seen: List[str] = []
        for uid in v:
            if uid and uid not in seen:
                seen.append(uid)
        return seen


class TaskEntityAssigneeOut(BaseModel):
    """One Team Member's completion row on a whole-entity-assigned task."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    task_id: str
    user_id: str
    done: bool
    done_at: Optional[datetime] = None
    due_date: Optional[date] = None
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None
    created_at: datetime
    # enriched
    user: Optional[UserMini] = None


class TaskEntityDoneIn(BaseModel):
    """Mark-done payload for a multi-assignee task — POST
    /tasks/{id}/mark-done-entity. Time Taken and Notes are mandatory:
    every assignee on a shared task (Lead included) leaves the same
    accountability trail a solo assignee already has to."""

    completion_notes: str
    completion_time_minutes: int

    @field_validator("completion_notes")
    @classmethod
    def _notes_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Notes are required")
        return v

    @field_validator("completion_time_minutes")
    @classmethod
    def _time_required(cls, v: int) -> int:
        if v is None or v <= 0:
            raise ValueError("Time taken must be greater than 0 minutes")
        return v


class CompletionLatenessMixin(BaseModel):
    """Adds `completed_at` + a derived "how late was it" to any task /
    sub-task output schema. `late_by_days` is the number of calendar days
    between the due date and the moment the item actually reached
    COMPLETED — 0 when it was on time, still open, or has no due date."""

    completed_at: Optional[datetime] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def late_by_days(self) -> int:
        due = getattr(self, "due_date", None)
        if (
            getattr(self, "status", None) != TaskStatus.COMPLETED
            or not self.completed_at
            or not due
        ):
            return 0
        return max(0, (self.completed_at.date() - due).days)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completed_late(self) -> bool:
        return self.late_by_days > 0


class TaskOut(CompletionLatenessMixin):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    plan_id: str
    entity_id: str
    # Only populated by call sites that bother to resolve it (e.g.
    # GET /dashboards/me/period-work, where a member's items can span
    # several entities) — None elsewhere rather than an extra query
    # every read path doesn't need.
    entity_name: Optional[str] = None
    # The owning Pack's calendar_type (AD/BS) — same enrich-only-where-useful
    # rule as entity_name. Lets a cross-pack view (e.g. GET
    # /dashboards/me/period-work, which spans every plan a member has work
    # in) resolve each task's own BS-vs-AD display without a separate pack
    # lookup per item; None where the call site doesn't populate it (the
    # plan itself already carries calendar_type for single-pack views).
    calendar_type: Optional[CalendarType] = None
    # The owning Pack's (Plan's) own title — same enrich-only-where-useful
    # rule. Never hardcode/derive a "pack name" from entity_name or
    # anywhere else; this is the single source of truth for which Pack a
    # task belongs to.
    plan_title: Optional[str] = None
    source_template_id: Optional[str] = None
    # Shared across every occurrence of ONE recurring custom task a
    # Lead/Admin created via "Add Task" with a frequency. Together with
    # source_template_id this lets the UI group a recurring series so it
    # can be assigned once instead of per-occurrence.
    recurring_group_id: Optional[str] = None
    is_custom: bool
    # Required (default) / Optional — see Task.is_required in models.py.
    is_required: bool = True
    assigned_to_id: Optional[str]
    member_assigned_to_id: Optional[str] = None
    assigned_entity_wide: bool = False
    title: str
    description: Optional[str]
    due_date: Optional[date]
    # WITHIN_PERIOD (default) / AFTER_PERIOD_END + the allowed number of
    # days past the period's end — see services/due_date_rules.py.
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0
    # N-Day Due Date — the raw occurrence date this due_date was computed
    # from (due_date = occurrence_date + due_days). None for a non-
    # recurring row. See services/due_date_rules.apply_due_days.
    occurrence_date: Optional[date] = None
    due_days: int = 0
    due_time: Optional[time] = None
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []
    status: TaskStatus
    rejection_note: Optional[str]
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None
    submitted_by_id: Optional[str] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Set while this recurring task was carried forward from a previous
    # period still incomplete; cleared once completed.
    carried_from_label: Optional[str] = None
    # enriched
    assigned_to: Optional[UserMini] = None
    member_assigned_to: Optional[UserMini] = None
    submitted_by: Optional[UserMini] = None
    entity_assignees: List[TaskEntityAssigneeOut] = []
    subtasks: List["SubTaskOut"] = []


class AttachmentOut(BaseModel):
    """A file attached to a Task or a SubTask. Exactly one of task_id /
    subtask_id is set. Fetch the bytes via the download endpoint (not
    included here — this is metadata only)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    task_id: Optional[str] = None
    subtask_id: Optional[str] = None
    uploaded_by_id: str
    filename: str
    content_type: Optional[str] = None
    size_bytes: int
    created_at: datetime
    # enriched
    uploaded_by: Optional[UserMini] = None


class CommentCreate(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def _non_empty_body(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Comment body cannot be empty")
        if len(v) > 4000:
            raise ValueError("Comment body cannot exceed 4000 characters")
        return v


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    task_id: str
    user_id: str
    body: str
    created_at: datetime
    # enriched
    user: Optional[UserMini] = None


class TaskDetailOut(CompletionLatenessMixin):
    """Enhanced task detail response including period info, progress, full
    subtask details, and the comment thread — everything the task detail
    side panel needs in one call."""
    model_config = ConfigDict(from_attributes=True)
    # Basic task info
    id: str
    tenant_id: str
    plan_id: str
    entity_id: str
    entity_name: Optional[str] = None
    source_template_id: Optional[str] = None
    recurring_group_id: Optional[str] = None
    # How many occurrences of this recurring task live in the SAME plan
    # (this one included). >1 means the "assign every occurrence" action
    # is available. 1 (or None) means it's a one-off.
    series_count: int = 1
    is_custom: bool
    assigned_to_id: Optional[str]
    member_assigned_to_id: Optional[str] = None
    assigned_entity_wide: bool = False
    title: str
    description: Optional[str]
    due_date: Optional[date]
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0
    occurrence_date: Optional[date] = None
    due_days: int = 0
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []
    status: TaskStatus
    rejection_note: Optional[str]
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None
    submitted_by_id: Optional[str] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    # Enriched user info
    assigned_to: Optional[UserMini] = None
    member_assigned_to: Optional[UserMini] = None
    submitted_by: Optional[UserMini] = None
    entity_assignees: List[TaskEntityAssigneeOut] = []
    subtasks: List["SubTaskOut"] = []

    # Period information
    period_id: Optional[str] = None
    period_label: Optional[str] = None
    period_start_date: Optional[date] = None
    period_end_date: Optional[date] = None
    # Days remaining until THIS TASK's own due_date (negative = overdue by
    # that many days), not the period's end date. None when due_date is unset.
    days_left: Optional[int] = None
    # Relative label ("Due today" / "Due this week" / ...), Nepal-date based.
    due_label: Optional[str] = None

    # Progress information — derived from sub-tasks; a leaf task (no
    # sub-tasks) reports 100% once COMPLETED, 0% otherwise.
    completed_subtasks: int = 0
    total_subtasks: int = 0
    progress_percentage: float = 0.0

    # Discussion thread
    comments: List[CommentOut] = []
    # Files attached directly to this task (not its sub-tasks' own).
    attachments: List[AttachmentOut] = []


class TaskAssignSeriesIn(BaseModel):
    """Assign (or clear) EVERY occurrence of a recurring task in the same
    plan at once — see POST /tasks/{id}/assign-series. 0 people clears the
    assignee on every occurrence; 1 = plain single assignment on each;
    2+ = multi-assignee on each. Occurrences already COMPLETED or
    PENDING_REVIEW are left untouched."""

    user_ids: List[str]

    @field_validator("user_ids")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        seen: List[str] = []
        for uid in v:
            if uid and uid not in seen:
                seen.append(uid)
        return seen


class TaskSeriesAssignOut(BaseModel):
    """Result of POST /tasks/{id}/assign-series."""

    updated: int
    skipped: int
    task: "TaskOut"


BULK_TASK_LIMIT = 200


def _dedupe_ids(v: List[str]) -> List[str]:
    seen: List[str] = []
    for x in v:
        if x and x not in seen:
            seen.append(x)
    return seen


class TaskBulkAssignIn(BaseModel):
    """Bulk assign / unassign — see POST /tasks/bulk-assign.

    `user_ids` empty -> unassign. `scope`:
      * "occurrence" — only the selected task rows are changed;
      * "series"     — each selected recurring task's WHOLE series is
        changed (once per series, however many of its occurrences were
        selected); non-recurring tasks fall back to "occurrence"."""

    task_ids: List[str] = Field(min_length=1, max_length=BULK_TASK_LIMIT)
    user_ids: List[str] = Field(default_factory=list)
    scope: Literal["occurrence", "series"] = "occurrence"

    @field_validator("task_ids", "user_ids")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        return _dedupe_ids(v)


class TaskBulkReviewIn(BaseModel):
    """Bulk approve / reject — see POST /tasks/bulk-review."""

    task_ids: List[str] = Field(min_length=1, max_length=BULK_TASK_LIMIT)
    decision: str  # APPROVE | REJECT
    note: Optional[str] = None  # required for REJECT

    @field_validator("task_ids")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        return _dedupe_ids(v)


class TaskBulkSkipOut(BaseModel):
    task_id: str
    title: Optional[str] = None
    reason: str


class TaskBulkResultOut(BaseModel):
    """`updated` counts task rows actually changed (in "series" scope that
    can exceed the number selected); `skipped` lists selected tasks that
    were left untouched, each with the reason."""

    updated: int
    skipped: List[TaskBulkSkipOut]


class SubTaskCreate(PostPeriodDueDaysMixin):
    task_id: str
    title: str
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    reviewer_id: Optional[str] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        return _normalize_tags(v)


class SubTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    reviewer_id: Optional[str] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    # Omit either to leave it unchanged — see TaskUpdate / PostPeriodDueDaysMixin.
    due_date_type: Optional[DueDateType] = None
    post_period_due_days: Optional[int] = None
    priority: Optional[Priority] = None
    status: Optional[TaskStatus] = None
    # Omit entirely to leave tags untouched; pass [] to clear them.
    tags: Optional[List[str]] = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return v if v is None else _normalize_tags(v)

    @field_validator("due_date_type")
    @classmethod
    def _retire_after_period_end(cls, v: Optional[DueDateType]) -> Optional[DueDateType]:
        return None if v is None else DueDateType.WITHIN_PERIOD

    @field_validator("post_period_due_days")
    @classmethod
    def _non_negative_post_period_days(cls, v: Optional[int]) -> Optional[int]:
        return None if v is None else 0


class SubTaskEntityAssigneeOut(BaseModel):
    """One Team Member's completion row on a multi-assignee sub-task —
    mirror of TaskEntityAssigneeOut."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    subtask_id: str
    user_id: str
    done: bool
    done_at: Optional[datetime] = None
    due_date: Optional[date] = None
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None
    created_at: datetime
    # enriched
    user: Optional[UserMini] = None


class SubTaskMultiAssignIn(BaseModel):
    """Assign a sub-task to a hand-picked set of people — POST
    /subtasks/{id}/assign-multi. Mirror of TaskMultiAssignIn, including
    the optional per-assignee `due_dates` (capped at the sub-task's own
    due date by the endpoint)."""

    user_ids: List[str]
    due_dates: Optional[Dict[str, Optional[date]]] = None

    @field_validator("user_ids")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Select at least one person to assign this sub-task to")
        seen: List[str] = []
        for uid in v:
            if uid and uid not in seen:
                seen.append(uid)
        return seen


class SubTaskAssignSeriesIn(BaseModel):
    """Assign (or clear) EVERY occurrence of a recurring task's matching
    sub-task at once — see POST /subtasks/{id}/assign-series. Mirror of
    TaskAssignSeriesIn, one level down: needed because a task-level
    series-assign skips any occurrence that carries sub-tasks (assignment
    for those lives here instead). 0 people clears the assignee on every
    occurrence."""

    user_ids: List[str]

    @field_validator("user_ids")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        seen: List[str] = []
        for uid in v:
            if uid and uid not in seen:
                seen.append(uid)
        return seen


class SubTaskEntityDoneIn(BaseModel):
    """Mark-done payload for a multi-assignee sub-task — POST
    /subtasks/{id}/mark-done-entity. Mirror of TaskEntityDoneIn."""

    completion_notes: str
    completion_time_minutes: int

    @field_validator("completion_notes")
    @classmethod
    def _notes_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Notes are required")
        return v

    @field_validator("completion_time_minutes")
    @classmethod
    def _time_required(cls, v: int) -> int:
        if v is None or v <= 0:
            raise ValueError("Time taken must be greater than 0 minutes")
        return v


class SubTaskOut(CompletionLatenessMixin):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    task_id: str
    entity_id: str
    # Same enrich-only-where-useful rule as TaskOut.entity_name — a
    # sub-task has no direct plan_id column (unlike Task), so this is only
    # ever populated by a call site that already resolved it (e.g. GET
    # /dashboards/me/period-work), for showing which Pack it belongs to.
    plan_id: Optional[str] = None
    # Same enrich-only-where-useful rule as TaskOut.entity_name.
    entity_name: Optional[str] = None
    # Same enrich-only-where-useful rule as TaskOut.calendar_type.
    calendar_type: Optional[CalendarType] = None
    # Same enrich-only-where-useful rule as TaskOut.plan_title.
    plan_title: Optional[str] = None
    source_template_id: Optional[str] = None
    is_custom: bool
    assigned_to_id: Optional[str]
    assigned_entity_wide: bool = False
    reviewer_id: Optional[str]
    title: str
    description: Optional[str]
    due_date: Optional[date]
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0
    # N-Day Due Date — the raw occurrence date this due_date was computed
    # from (due_date = occurrence_date + due_days). None for a non-
    # recurring row. See services/due_date_rules.apply_due_days.
    occurrence_date: Optional[date] = None
    due_days: int = 0
    due_time: Optional[time] = None
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []
    status: TaskStatus
    rejection_note: Optional[str]
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None
    submitted_by_id: Optional[str] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    carried_from_label: Optional[str] = None
    # How many occurrences of the parent recurring task (in the same plan)
    # carry this same sub-task (matched by sub-task template, or by title
    # for a custom recurring task's sub-tasks). >1 means the "assign every
    # occurrence" action is available for this sub-task. Only populated by
    # GET /tasks/{id}/detail — other read paths leave it at the default 1.
    series_count: int = 1
    # enriched
    assigned_to: Optional[UserMini] = None
    reviewer: Optional[UserMini] = None
    submitted_by: Optional[UserMini] = None
    # Populated only where the multi-assignee relationship was eager-loaded
    # (Lead task detail, member views); [] elsewhere.
    entity_assignees: List[SubTaskEntityAssigneeOut] = []
    # Only populated by GET /tasks/{id}/detail (each nested subtask's own
    # attachments) — other read paths leave this [] rather than pay for
    # an extra query they don't need.
    attachments: List[AttachmentOut] = []


class SubTaskSeriesAssignOut(BaseModel):
    """Result of POST /subtasks/{id}/assign-series."""

    updated: int
    skipped: int
    subtask: SubTaskOut


class SubTaskReviewIn(BaseModel):
    decision: str  # APPROVE | REJECT
    note: Optional[str] = None


class SubTaskSubmitIn(BaseModel):
    completion_notes: Optional[str] = None
    completion_time_minutes: Optional[int] = None

    @field_validator("completion_time_minutes")
    @classmethod
    def validate_time(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 0:
            raise ValueError("completion_time_minutes must be non-negative")
        return value


# ============================================================
# Carry forward
# ============================================================
class CarryForwardItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    group_id: str
    original_plan_id: str
    original_task_id: Optional[str]
    original_subtask_id: Optional[str]
    original_assignee_id: Optional[str]
    original_entity_id: str
    item_type: str
    title: str
    snapshot_json: Dict[str, Any]
    approved: Optional[bool]
    copied_to_task_id: Optional[str]
    copied_to_subtask_id: Optional[str]
    created_at: datetime


class CarryForwardGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    from_period_id: str
    to_period_id: Optional[str]
    status: CarryForwardStatus
    notes: Optional[str]
    created_at: datetime
    items: List[CarryForwardItemOut] = []


class CarryForwardDecisionIn(BaseModel):
    decision: str  # APPROVE | REJECT
    notes: Optional[str] = None
    item_ids: Optional[List[str]] = None  # default = all items


# ============================================================
# Dashboards
# ============================================================
class TenantDashboardOut(BaseModel):
    total_plans: int
    open_plans: int
    closed_plans: int
    completion_percentage: float
    overdue_tasks: int
    carry_forward_count: int
    total_tasks: int
    completed_tasks: int


class EntityDashboardOut(BaseModel):
    entity_id: str
    entity_name: str
    completion_percentage: float
    overdue_tasks: int
    pending_reviews: int
    total_tasks: int
    completed_tasks: int


class TeamLeadDashboardOut(BaseModel):
    user_id: str
    assigned_tasks: int
    pending_reviews: int
    completed_tasks: int
    overdue_tasks: int


class MyPeriodWorkOut(BaseModel):
    """
    Unified per-user work view for a period.

    Returned by GET /api/dashboards/me/period-work. Includes both template-
    sourced items (is_custom=false) AND lead-created custom items
    (is_custom=true) — the customization layer is transparent to the user.

    A task appears here only when assigned_to_id equals the caller. A
    subtask may surface independently if the caller is its assignee or
    reviewer even when the parent task is owned by someone else.
    """

    period_id: str
    period_label: Optional[str] = None
    tasks: List["TaskOut"] = []
    subtasks: List["SubTaskOut"] = []


class LeadWorkbenchPlanOut(BaseModel):
    """One plan in the Team Lead workbench tree, with full drill-down."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    period_id: str
    entity_id: str
    owner_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: PlanStatus
    due_date: Optional[date] = None
    entity_name: Optional[str] = None
    tasks: List["TaskOut"] = []


class LeadWorkbenchOut(BaseModel):
    """
    Team-Lead structural workbench for an OPEN period.

    Returns every plan the lead may structurally edit (owned plans + plans
    on entities in their scope) plus their full task / subtask tree so
    the lead can perform create / update / delete / assignment actions in
    one screen. Custom items (is_custom=true) and template items are
    intermixed; the flag lets the UI badge them differently.
    """

    period_id: str
    period_label: Optional[str] = None
    plans: List[LeadWorkbenchPlanOut] = []


# ============================================================
# Team workload + task board (Lead/Admin task management surface)
# ============================================================
class TeamWorkloadMemberOut(BaseModel):
    """One team member's open-work load for a period.

    `load_pct` = share of this person's assigned tasks+subtasks (in the
    period) that are still open (not COMPLETED/CARRIED_FORWARD) — i.e.
    how much of what they're on the hook for is still outstanding. Each
    member's figure is independent (they don't sum to 100% across the
    team) — >80% is flagged as overloaded, matching the UI's callout.
    """

    user_id: str
    name: str
    role: Role
    open_count: int
    total_count: int
    load_pct: float
    overloaded: bool = False


class TeamWorkloadOut(BaseModel):
    period_id: Optional[str] = None
    period_label: Optional[str] = None
    members: List[TeamWorkloadMemberOut] = []


class TaskBoardCountsOut(BaseModel):
    """Status-bucket counts for the currently-filtered task board — every
    task/subtask (per `item_types`) falls into exactly one bucket."""

    all: int = 0
    not_started: int = 0
    in_progress: int = 0
    due_today: int = 0
    overdue: int = 0
    completed: int = 0
    pending_review: int = 0


class TaskBoardItemOut(BaseModel):
    """One row on the task board — a Task (with its subtasks nested) or a
    standalone SubTask row, flattened with entity/period context so the
    board doesn't need N+1 lookups."""

    item_type: str  # TASK | SUBTASK
    id: str
    plan_id: str
    entity_id: str
    entity_name: Optional[str] = None
    parent_task_id: Optional[str] = None
    parent_task_title: Optional[str] = None
    title: str
    description: Optional[str] = None
    is_custom: bool = False
    # False = Optional: listed here but never part of `counts` / the totals.
    is_required: bool = True
    assigned_to_id: Optional[str] = None
    assigned_to: Optional[UserMini] = None
    due_date: Optional[date] = None
    due_date_type: DueDateType = DueDateType.WITHIN_PERIOD
    post_period_due_days: int = 0
    occurrence_date: Optional[date] = None
    due_days: int = 0
    priority: Priority = Priority.MEDIUM
    tags: List[str] = []
    status: TaskStatus
    bucket: str  # not_started | in_progress | due_today | overdue | completed | pending_review
    subtask_count: int = 0
    subtask_done_count: int = 0
    progress_pct: float = 0.0
    subtasks: List["SubTaskOut"] = []


class TaskBoardOut(BaseModel):
    period_id: Optional[str] = None
    period_label: Optional[str] = None
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    plan_id: Optional[str] = None
    plan_title: Optional[str] = None
    plan_due_date: Optional[date] = None
    total_count: int = 0
    completed_count: int = 0
    progress_pct: float = 0.0
    counts: TaskBoardCountsOut = TaskBoardCountsOut()
    tasks: List[TaskBoardItemOut] = []


# ============================================================
# Team Dashboard (GET /api/team/*) — role-aware summary + actions
# ============================================================
class TeamDashboardOut(BaseModel):
    """Role-aware team dashboard stats. For a Lead/Admin this is
    tenant/ownership-scoped across their packs, with a per-member
    workload breakdown; for a Team Member it's their own assigned work
    only, with an empty `team_workload` (they have no team to see)."""

    role: str
    period_id: Optional[str] = None
    period_label: Optional[str] = None
    pack_count: int = 0
    task_count: int = 0
    counts: TaskBoardCountsOut = TaskBoardCountsOut()
    team_workload: List[TeamWorkloadMemberOut] = []
    # The caller's own logged minutes for *today* (Nepal-local date), from
    # TimeEntry — independent of `period_id`/period scoping above, and
    # always the caller's own total even for a Lead/Admin (never a team
    # sum). Feeds the "Time Logged Today" banner metric.
    time_logged_today_minutes: int = 0


class TeamAssignIn(BaseModel):
    """Body for POST/PATCH .../assign[ment] — assigns or reassigns the
    work item (task or sub-task, whichever `{task_id}` resolves to) to
    a Team Member."""

    assignee_id: str


class TeamStatusIn(BaseModel):
    """Body for PATCH .../status. Deliberately narrow: from here a Team
    Member may only move a NOT_STARTED/ASSIGNED/REJECTED item to
    IN_PROGRESS ("start work"). Reaching PENDING_REVIEW requires
    POST .../submit (captures completion notes/time); reaching
    COMPLETED requires a Lead's review — status can't skip either step
    through this endpoint."""

    status: TaskStatus


class TeamSubmitIn(BaseModel):
    """Body for POST .../submit — the completion note and time spent
    collected when a Team Member submits their work for review."""

    comment: Optional[str] = None
    time_minutes: Optional[int] = None

    @field_validator("time_minutes")
    @classmethod
    def _non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("time_minutes must be non-negative")
        return v


class SubmissionHistoryEntryOut(BaseModel):
    """One audit-trail event for a task/sub-task — submission, approval,
    rejection, assignment, status change — powering both the Lead's
    'task submissions' view and a Team Member's own status history."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    action: str
    user_id: Optional[str] = None
    user: Optional[UserMini] = None
    meta: Optional[Dict[str, Any]] = None
    created_at: datetime


# ============================================================
# Reports
# ============================================================
class ReportRowOut(BaseModel):
    data: List[Dict[str, Any]]
    columns: List[str]
    generated_at: datetime
    report_type: str


class ReportEnvelopeOut(BaseModel):
    """
    Rich JSON envelope returned by the Report Engine.

    `rows` is the tabular body (also rendered by CSV); `summary` carries
    aggregate / header metrics that are NOT part of the row stream
    (e.g. the User Workload scorecard, the Year-End trend line).
    `columns` is the ordered CSV column list (also drives the PDF table
    header).
    """

    report_type: str
    generated_at: datetime
    generated_by: Optional[UserMini] = None
    tenant_id: str
    tenant_name: Optional[str] = None
    period_id: Optional[str] = None
    period_label: Optional[str] = None
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)


class ReportSnapshotOut(BaseModel):
    """A persisted report record (audit evidence)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    report_type: str
    period_id: Optional[str] = None
    entity_id: Optional[str] = None
    target_user_id: Optional[str] = None
    generated_by_id: Optional[str] = None
    trigger: str
    parameters: Optional[Dict[str, Any]] = None
    payload_json: Dict[str, Any]
    generated_at: datetime


# ============================================================
# Time entries
# ============================================================
class TimeEntryCreate(BaseModel):
    """
    Log a block of time against a work item.

    Exactly ONE of (task_id, subtask_id) must be provided. `entity_id` is
    derived from the chosen work item by the router. `entry_date`
    defaults to today when omitted.
    """

    task_id: Optional[str] = None
    subtask_id: Optional[str] = None
    entry_date: Optional[date] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_minutes: int
    note: Optional[str] = None

    @field_validator("duration_minutes")
    @classmethod
    def _positive_duration(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("duration_minutes must be > 0")
        if v > 24 * 60:
            raise ValueError(
                "duration_minutes cannot exceed 24 hours in a single entry"
            )
        return v


class TimeEntryUpdate(BaseModel):
    entry_date: Optional[date] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    note: Optional[str] = None


class TimeEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    user_id: str
    entity_id: str
    task_id: Optional[str] = None
    subtask_id: Optional[str] = None
    period_id: Optional[str] = None
    entry_date: date
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_minutes: int
    note: Optional[str] = None
    created_at: datetime
    # enriched
    user: Optional[UserMini] = None
    entity_name: Optional[str] = None
    work_item_title: Optional[str] = None
    work_item_type: Optional[str] = None  # TASK | SUBTASK


# ============================================================
# Audit
# ============================================================
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    user_id: Optional[str]
    action: str
    entity_type: str
    entity_id: Optional[str]
    meta: Optional[Dict[str, Any]]
    created_at: datetime


# ============================================================
# Notifications
# ============================================================
class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    user_id: str
    type: str
    message: str
    link: Optional[str]
    read: bool
    created_at: datetime



# Resolve forward refs
EntityGroupTreeOut.model_rebuild()
PlanOut.model_rebuild()
TaskOut.model_rebuild()
TaskSeriesAssignOut.model_rebuild()
SubTaskOut.model_rebuild()
PackReportTaskRow.model_rebuild()
MyPeriodWorkOut.model_rebuild()
LeadWorkbenchPlanOut.model_rebuild()
LeadWorkbenchOut.model_rebuild()


class TimeDayOut(BaseModel):
    date: date
    minutes: int


class TimeWeekOut(BaseModel):
    week_start: date
    minutes: int


class TimeMonthOut(BaseModel):
    month_start: date
    minutes: int


class MyTimeSummaryOut(BaseModel):
    """The caller's own logged time — see GET /api/time-entries/my/summary.

    Every bucket is in the Nepal-local calendar (`entry_date`). Weeks start
    on Sunday. `daily` is contiguous (days with nothing logged are 0) so a
    chart can plot it as-is; `weekly` / `monthly` are oldest -> newest and
    end with the current (partial) week / month.

    `user` is set only when a Team Lead views a team member's summary
    (GET /api/time-entries/user/{id}/summary); it is None for the caller's own."""

    user: Optional[UserMini] = None
    today: date
    week_start: date
    month_start: date
    today_minutes: int
    week_minutes: int
    month_minutes: int
    range_days: int
    range_total_minutes: int
    daily: List[TimeDayOut]
    weekly: List[TimeWeekOut]
    monthly: List[TimeMonthOut]


class HrmsProjectOut(BaseModel):
    """One HRMS project — see GET /api/hrms/projects."""

    id: int
    name: str


class HrmsSubActivityOut(BaseModel):
    id: int
    name: str
    key: Optional[str] = None
    description: Optional[str] = None
    data_type: Optional[int] = None


class HrmsActivityOut(BaseModel):
    """One HRMS activity — see GET /api/hrms/activities. An activity with a
    non-empty `sub_activities` needs one of them chosen as well."""

    id: int
    name: str
    description: Optional[str] = None
    sub_activities: List[HrmsSubActivityOut] = Field(default_factory=list)


# ============================================================
# Automated HRMS timesheet  (GET/POST /api/timesheets/...)
# ============================================================
TimesheetWorkType = Literal["BILLABLE", "NON_BILLABLE", "PAID_NON_BILLABLE"]


class TimesheetSuggestionOut(BaseModel):
    activity_id: int
    score: float
    reason: str


class TimesheetSourceOut(BaseModel):
    """The task / sub task whose submission a row files."""

    kind: Literal["task", "subtask"]
    unit_id: str
    minutes: int
    task_title: str
    subtask_title: Optional[str] = None


class TimesheetLineOut(BaseModel):
    """One proposed HRMS entry. `key` identifies it for the submit call."""

    key: str
    work_date: date
    minutes: int
    hours: float
    status: Literal["MATCHED", "NEEDS_SUB", "AMBIGUOUS", "UNMATCHED"]
    tier: Optional[str] = None
    score: float = 0.0
    reason: str = ""
    hrms_activity_id: Optional[int] = None
    # For an activity with sub activities: {sub activity id: value}; "" = still to fill in.
    sub_values: Dict[int, str] = Field(default_factory=dict)
    suggestions: List[TimesheetSuggestionOut] = Field(default_factory=list)
    summary: str = ""
    work_type: TimesheetWorkType = "BILLABLE"
    sources: List[TimesheetSourceOut] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)
    selected: bool = False
    existing_hours: Optional[float] = None
    # Time Taken was left blank when this was submitted: minutes is 0 until the user types hours.
    needs_hours: bool = False


class TimesheetIssueOut(BaseModel):
    code: str
    message: str


class TimesheetDayOut(BaseModel):
    """Every date of the period is listed, with or without tasks."""

    date: date
    non_working: bool = False
    label: Optional[str] = None   # "Weekend" or the holiday's name


class TimesheetExistingOut(BaseModel):
    work_date: date
    hrms_activity_id: int
    hours: float
    origin: Literal["HRMS", "CLOSELOOP"]


class TimesheetTotalsOut(BaseModel):
    tasks: int
    reported_minutes: int
    hours_missing: int
    ready_minutes: int
    already_in_hrms_minutes: int
    needs_review_minutes: int
    held_minutes: int
    selected_minutes: int
    already_filed_tasks: int
    already_filed_minutes: int


class TimesheetPreviewOut(BaseModel):
    hrms_project_id: int
    hrms_project_name: Optional[str] = None
    month: str
    range_start: date
    range_end: date
    employee_id: Optional[int] = None
    submit_enabled: bool
    can_submit: bool
    # False for a POST-only HRMS: entries can be sent but not read back.
    hrms_entries_readable: bool = False
    lines: List[TimesheetLineOut] = Field(default_factory=list)
    existing_entries: List[TimesheetExistingOut] = Field(default_factory=list)
    days: List[TimesheetDayOut] = Field(default_factory=list)
    blockers: List[TimesheetIssueOut] = Field(default_factory=list)
    warnings: List[TimesheetIssueOut] = Field(default_factory=list)
    totals: TimesheetTotalsOut


class TimesheetLineChoice(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    hrms_activity_id: int
    # {sub activity id: value} - every sub activity of the activity needs a value.
    sub_values: Dict[int, str] = Field(default_factory=dict)
    summary: str = Field(default="", max_length=5000)
    work_type: TimesheetWorkType = "BILLABLE"
    # Required for a task with no time logged (the hours the user typed); not allowed to
    # differ from the logged time otherwise.
    minutes: Optional[int] = Field(default=None, ge=1, le=1440)


class TimesheetSubmitIn(BaseModel):
    hrms_project_id: int
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    through: Optional[date] = None
    idempotency_key: str = Field(min_length=8, max_length=100)
    lines: List[TimesheetLineChoice] = Field(min_length=1, max_length=400)
    acknowledge_warnings: bool = False


class TimesheetSubmittedLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    work_date: date
    hrms_activity_id: int
    sub_values: Optional[Dict[int, str]] = None
    minutes: int
    hours: float
    summary: str
    work_type: str
    status: Literal["PENDING", "SENT", "FAILED", "UNCERTAIN"]
    hrms_entry_id: Optional[str] = None
    error: Optional[str] = None


class TimesheetSubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    hrms_project_id: int
    hrms_project_name: str
    hrms_email: Optional[str] = None
    range_start: date
    range_end: date
    status: Literal["SUBMITTING", "SUBMITTED", "PARTIAL", "FAILED"]
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    lines: List[TimesheetSubmittedLineOut] = Field(default_factory=list)


class TimesheetResolveIn(BaseModel):
    outcome: Literal["SENT", "NOT_SENT"]


class TimesheetObligationOut(BaseModel):
    calendar: Literal["AD", "BS"]
    period_key: str
    label: str
    start: date
    end: date
    deadline: date
    status: Literal["UPCOMING", "DUE", "OVERDUE"]
    days_left: int
    days_overdue: int
    pending_tasks: int
    pending_minutes: int
