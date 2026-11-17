"""Close Pack service — manages versioning + snapshot of a pack's template tree."""

from __future__ import annotations
from datetime import date, datetime, time
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from ..models import (
    ClosePack,
    ClosePackVersion,
    TaskTemplate,
    SubTaskTemplate,
)
from ..schemas import TaskTemplateIn, SubTaskTemplateIn
from .due_date_rules import resolve_generated_due_date
from .period_service import get_current_period


async def load_pack_tree(
    db: AsyncSession, tenant_id: str, pack_id: str
) -> Optional[ClosePack]:
    result = await db.execute(
        select(ClosePack).where(
            ClosePack.id == pack_id, ClosePack.tenant_id == tenant_id
        )
    )
    return result.scalar_one_or_none()


async def load_task_templates(
    db: AsyncSession, tenant_id: str, pack_id: str
) -> list[TaskTemplate]:
    result = await db.execute(
        select(TaskTemplate)
        .where(
            TaskTemplate.tenant_id == tenant_id,
            TaskTemplate.close_pack_id == pack_id,
        )
        .options(selectinload(TaskTemplate.sub_task_templates))
        .order_by(TaskTemplate.order, TaskTemplate.created_at)
    )
    return list(result.scalars().all())


def _tmpl_timing(tmpl) -> dict:
    return {"due_time": tmpl.due_time.isoformat() if tmpl.due_time else None}


def build_snapshot_payload(pack: ClosePack, task_templates: list[TaskTemplate]) -> dict:
    """The pure dict-shape a ClosePackVersion snapshot captures — pulled out
    of `snapshot_pack` so a caller that only wants "the pack's CURRENT
    template tree as plain dicts" (e.g. a period preview computation) can
    get it without writing a new ClosePackVersion row. Same shape either
    way, so `plan_generation_service`'s pure date-math (which expects this
    dict shape) works identically on a real snapshot or a live preview."""
    return {
        "pack": {
            "id": pack.id,
            "name": pack.name,
            "description": pack.description,
            "calendar_type": pack.calendar_type.value,
        },
        "task_templates": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "due_day_of_period": t.due_day_of_period,
                "due_anchor": t.due_anchor.value if t.due_anchor else "PERIOD_START",
                "due_date_type": t.due_date_type.value if t.due_date_type else "WITHIN_PERIOD",
                "post_period_due_days": t.post_period_due_days or 0,
                "due_days": t.due_days or 0,
                "frequency": t.frequency.value if t.frequency else None,
                "is_required": t.is_required,
                "interval_count": t.interval_count,
                "days_of_week": list(t.days_of_week) if t.days_of_week else None,
                "recurrence_start": t.recurrence_start.isoformat() if t.recurrence_start else None,
                "recurrence_end": t.recurrence_end.isoformat() if t.recurrence_end else None,
                "reviewer_required": t.reviewer_required,
                "tags": list(t.tags) if t.tags else [],
                "priority": t.priority.value if t.priority else "MEDIUM",
                "order": t.order,
                **_tmpl_timing(t),
                "sub_task_templates": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "description": s.description,
                        "due_day_of_period": s.due_day_of_period,
                        "due_anchor": s.due_anchor.value if s.due_anchor else "PERIOD_START",
                        "due_date_type": s.due_date_type.value if s.due_date_type else "WITHIN_PERIOD",
                        "post_period_due_days": s.post_period_due_days or 0,
                        "due_days": s.due_days or 0,
                        "frequency": s.frequency.value if s.frequency else None,
                        "interval_count": s.interval_count,
                        "days_of_week": list(s.days_of_week) if s.days_of_week else None,
                        "recurrence_start": s.recurrence_start.isoformat() if s.recurrence_start else None,
                        "recurrence_end": s.recurrence_end.isoformat() if s.recurrence_end else None,
                        "order": s.order,
                        **_tmpl_timing(s),
                    }
                    for s in t.sub_task_templates
                ],
            }
            for t in task_templates
        ],
    }


async def snapshot_pack(
    db: AsyncSession,
    tenant_id: str,
    pack: ClosePack,
    user_id: Optional[str] = None,
) -> ClosePackVersion:
    """Capture the current template tree as a new immutable ClosePackVersion."""
    task_templates = await load_task_templates(db, tenant_id, pack.id)
    payload = build_snapshot_payload(pack, task_templates)

    # next version_number
    result = await db.execute(
        select(ClosePackVersion.version_number)
        .where(ClosePackVersion.close_pack_id == pack.id)
        .order_by(desc(ClosePackVersion.version_number))
        .limit(1)
    )
    last = result.scalar_one_or_none()
    next_version = (last or 0) + 1

    version = ClosePackVersion(
        tenant_id=tenant_id,
        close_pack_id=pack.id,
        version_number=next_version,
        snapshot_json=payload,
        created_by_id=user_id,
    )
    db.add(version)
    pack.current_version = next_version
    await db.flush()
    return version


async def get_latest_version(
    db: AsyncSession, tenant_id: str, pack_id: str
) -> Optional[ClosePackVersion]:
    result = await db.execute(
        select(ClosePackVersion)
        .where(
            ClosePackVersion.close_pack_id == pack_id,
            ClosePackVersion.tenant_id == tenant_id,
        )
        .order_by(desc(ClosePackVersion.version_number))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_version(
    db: AsyncSession, tenant_id: str, pack_id: str, version_number: int
) -> Optional[ClosePackVersion]:
    result = await db.execute(
        select(ClosePackVersion).where(
            ClosePackVersion.close_pack_id == pack_id,
            ClosePackVersion.version_number == version_number,
            ClosePackVersion.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


# ==========================================================================
# Bulk task/subtask import — reads the "Tasks & Subtasks" sheet produced by
# the admin UI's "Download template" button (frontend/src/pages/
# ClosePacksPage.jsx) and turns it into TaskTemplate/SubTaskTemplate rows.
# Additive: appends to whatever the pack already has, never deletes.
# ==========================================================================

_WEEKDAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
_TASK_TEMPLATE_COLUMNS = (
    "row type", "task key", "parent task key", "title", "description",
    "priority", "reviewer required", "tags",
    "frequency", "interval count", "days of week", "recurrence start",
    "recurrence end", "due days", "due day of period", "due anchor",
    "due date type", "post period due days", "due time", "order",
)


def _cell_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _row_is_blank(row: list) -> bool:
    return all(_cell_str(c) == "" for c in row)


def _match_template_column(headers: list[str], name: str) -> Optional[int]:
    """Match a column by its header text, ignoring a trailing format hint
    like "Recurrence Start (YYYY-MM-DD)" — matches on the plain name."""
    target = name.strip().lower()
    for i, h in enumerate(headers):
        if _cell_str(h).lower().startswith(target):
            return i
    return None


def _parse_bulk_date(value: Any) -> Optional[date]:
    if value is None or _cell_str(value) == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _cell_str(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date '{value}'")


def _parse_bulk_time(value: Any) -> Optional[time]:
    if value is None or _cell_str(value) == "":
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = _cell_str(value)
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"unrecognized time '{value}'")


def _parse_days_of_week(value: Any) -> Optional[list[int]]:
    text = _cell_str(value)
    if not text:
        return None
    out: list[int] = []
    for part in text.split(","):
        key = part.strip().lower()[:3]
        if key not in _WEEKDAY_MAP:
            raise ValueError(f"unrecognized weekday '{part.strip()}'")
        out.append(_WEEKDAY_MAP[key])
    return out or None


def _parse_tags(value: Any) -> list[str]:
    text = _cell_str(value)
    return [t.strip() for t in text.split(",") if t.strip()] if text else []


def _parse_yes_no(value: Any) -> bool:
    return _cell_str(value).lower() in ("yes", "y", "true", "1")


def _token_or_default(value: Any, default: str) -> str:
    text = _cell_str(value)
    return text.upper().replace(" ", "_").replace("-", "_") if text else default


def _int_or_default(value: Any, default: int) -> int:
    text = _cell_str(value)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        raise ValueError(f"'{value}' is not a whole number")


def _int_or_none(value: Any) -> Optional[int]:
    text = _cell_str(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        raise ValueError(f"'{value}' is not a whole number")


def _pydantic_error_message(e: ValidationError) -> str:
    first = e.errors()[0]
    field = ".".join(str(p) for p in first["loc"])
    return f"{field}: {first['msg']}"


class ParsedTaskRow:
    __slots__ = ("row_number", "task_key", "payload")

    def __init__(self, row_number: int, task_key: str, payload: TaskTemplateIn):
        self.row_number = row_number
        self.task_key = task_key
        self.payload = payload


class ParsedSubTaskRow:
    __slots__ = ("row_number", "parent_task_key", "payload")

    def __init__(self, row_number: int, parent_task_key: str, payload: SubTaskTemplateIn):
        self.row_number = row_number
        self.parent_task_key = parent_task_key
        self.payload = payload


def parse_task_template_rows(
    rows: list[list],
) -> tuple[list[ParsedTaskRow], list[ParsedSubTaskRow], list[str]]:
    """Turn the raw "Tasks & Subtasks" sheet rows into validated
    TaskTemplateIn/SubTaskTemplateIn payloads, in file order. A Subtask row
    must appear AFTER the Task row whose Task Key it references — this is
    checked here so a Subtask can never point at a task import will never
    create. Returns (tasks, subtasks, errors); when `errors` is non-empty
    nothing should be written — the caller shows every error at once so the
    file can be fixed in one pass instead of a trial-and-error loop."""
    tasks: list[ParsedTaskRow] = []
    subtasks: list[ParsedSubTaskRow] = []
    errors: list[str] = []

    rows = [r for r in rows if r is not None and not _row_is_blank(r)]
    if not rows:
        return tasks, subtasks, ["File has no rows"]

    headers = [_cell_str(c) for c in rows[0]]
    col = {name: _match_template_column(headers, name) for name in _TASK_TEMPLATE_COLUMNS}
    missing = [name for name in ("row type", "title") if col[name] is None]
    if missing:
        return tasks, subtasks, [
            "Header row is missing required column(s): " + ", ".join(missing)
        ]

    def cell(row: list, name: str) -> Any:
        idx = col[name]
        return row[idx] if idx is not None and idx < len(row) else None

    seen_task_keys: set[str] = set()

    for i, row in enumerate(rows[1:], start=2):
        row_type = _cell_str(cell(row, "row type")).upper()
        if row_type not in ("TASK", "SUBTASK"):
            errors.append(f"Row {i}: Row Type must be \"Task\" or \"Subtask\", got '{row_type}'")
            continue
        title = _cell_str(cell(row, "title"))
        if not title:
            errors.append(f"Row {i}: Title is required")
            continue

        try:
            shared = dict(
                title=title,
                description=_cell_str(cell(row, "description")) or None,
                frequency=_token_or_default(cell(row, "frequency"), "") or None,
                interval_count=_int_or_default(cell(row, "interval count"), 1),
                days_of_week=_parse_days_of_week(cell(row, "days of week")),
                recurrence_start=_parse_bulk_date(cell(row, "recurrence start")),
                recurrence_end=_parse_bulk_date(cell(row, "recurrence end")),
                due_anchor=_token_or_default(cell(row, "due anchor"), "PERIOD_START"),
                due_date_type=_token_or_default(cell(row, "due date type"), "WITHIN_PERIOD"),
                post_period_due_days=_int_or_default(cell(row, "post period due days"), 0),
                due_days=_int_or_default(cell(row, "due days"), 0),
                due_time=_parse_bulk_time(cell(row, "due time")),
                order=_int_or_default(cell(row, "order"), 0),
            )

            if row_type == "TASK":
                task_key = _cell_str(cell(row, "task key"))
                if not task_key:
                    errors.append(f"Row {i}: Task rows need a Task Key")
                    continue
                if task_key in seen_task_keys:
                    errors.append(f"Row {i}: duplicate Task Key '{task_key}'")
                    continue
                seen_task_keys.add(task_key)
                payload = TaskTemplateIn(
                    **shared,
                    due_day_of_period=_int_or_none(cell(row, "due day of period")),
                    reviewer_required=_parse_yes_no(cell(row, "reviewer required")),
                    tags=_parse_tags(cell(row, "tags")),
                    priority=_token_or_default(cell(row, "priority"), "MEDIUM"),
                )
                tasks.append(ParsedTaskRow(i, task_key, payload))
            else:
                parent_key = _cell_str(cell(row, "parent task key"))
                if not parent_key:
                    errors.append(f"Row {i}: Subtask rows need a Parent Task Key")
                    continue
                if parent_key not in seen_task_keys:
                    errors.append(
                        f"Row {i}: Parent Task Key '{parent_key}' doesn't match any "
                        "Task row above it"
                    )
                    continue
                due_day = _int_or_none(cell(row, "due day of period"))
                if shared["frequency"] is None and due_day is None:
                    errors.append(
                        f"Row {i}: give this subtask a Due Day Of Period or a Frequency"
                    )
                    continue
                payload = SubTaskTemplateIn(**shared, due_day_of_period=due_day)
                subtasks.append(ParsedSubTaskRow(i, parent_key, payload))
        except ValidationError as e:
            errors.append(f"Row {i}: {_pydantic_error_message(e)}")
        except ValueError as e:
            errors.append(f"Row {i}: {e}")

    return tasks, subtasks, errors


async def import_task_templates(
    db: AsyncSession,
    tenant_id: str,
    pack_id: str,
    rows: list[list],
    user_id: str,
) -> dict:
    """Parse + validate the whole sheet first; only if it's entirely clean
    does this write anything (all-or-nothing), then appends the new tasks
    and subtasks to the pack. Raises ValueError (caller turns this into a
    400) with every row error joined together when the file has problems."""
    tasks, subtasks, errors = parse_task_template_rows(rows)
    if errors:
        raise ValueError("; ".join(errors[:20]) + (" …" if len(errors) > 20 else ""))
    if not tasks:
        raise ValueError("No task rows found in file")

    current_period = await get_current_period(db, tenant_id)

    created_tasks: dict[str, TaskTemplate] = {}
    for parsed in tasks:
        t = TaskTemplate(
            tenant_id=tenant_id,
            close_pack_id=pack_id,
            title=parsed.payload.title,
            description=parsed.payload.description,
            due_day_of_period=parsed.payload.due_day_of_period,
            due_anchor=parsed.payload.due_anchor,
            due_date_type=parsed.payload.due_date_type,
            post_period_due_days=parsed.payload.post_period_due_days,
            due_days=parsed.payload.due_days,
            reviewer_required=parsed.payload.reviewer_required,
            order=parsed.payload.order,
            frequency=parsed.payload.frequency,
            interval_count=parsed.payload.interval_count,
            days_of_week=parsed.payload.days_of_week,
            recurrence_start=parsed.payload.recurrence_start,
            recurrence_end=parsed.payload.recurrence_end,
            due_time=parsed.payload.due_time,
            tags=parsed.payload.tags,
            priority=parsed.payload.priority,
        )
        db.add(t)
        created_tasks[parsed.task_key] = t
    await db.flush()

    schedule_errors: list[str] = []
    for parsed in subtasks:
        parent = created_tasks[parsed.parent_task_key]
        if current_period and parsed.payload.frequency is None and parent.frequency is None:
            parent_due = resolve_generated_due_date(
                current_period.start_date, current_period.end_date,
                parent.due_day_of_period, parent.due_anchor,
                parent.due_date_type, parent.post_period_due_days,
            )
            sub_due = resolve_generated_due_date(
                current_period.start_date, current_period.end_date,
                parsed.payload.due_day_of_period, parsed.payload.due_anchor,
                parsed.payload.due_date_type, parsed.payload.post_period_due_days,
            )
            if sub_due > parent_due:
                schedule_errors.append(
                    f"Row {parsed.row_number}: would be due {sub_due.isoformat()} — after "
                    f"its parent task's due date {parent_due.isoformat()}"
                )
                continue
        s = SubTaskTemplate(
            tenant_id=tenant_id,
            task_template_id=parent.id,
            title=parsed.payload.title,
            description=parsed.payload.description,
            due_day_of_period=parsed.payload.due_day_of_period,
            due_anchor=parsed.payload.due_anchor,
            due_date_type=parsed.payload.due_date_type,
            post_period_due_days=parsed.payload.post_period_due_days,
            due_days=parsed.payload.due_days,
            order=parsed.payload.order,
            due_time=parsed.payload.due_time,
            frequency=parsed.payload.frequency,
            interval_count=parsed.payload.interval_count,
            days_of_week=parsed.payload.days_of_week,
            recurrence_start=parsed.payload.recurrence_start,
            recurrence_end=parsed.payload.recurrence_end,
        )
        db.add(s)

    if schedule_errors:
        await db.rollback()
        raise ValueError(
            "; ".join(schedule_errors[:20]) + (" …" if len(schedule_errors) > 20 else "")
        )

    await db.flush()
    return {"tasks_created": len(created_tasks), "subtasks_created": len(subtasks)}
