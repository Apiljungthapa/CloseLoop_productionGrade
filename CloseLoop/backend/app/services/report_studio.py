"""Report Studio — rich, insight-bearing reports (KPIs, charts, trends, findings, actions).

The classic reports in ``report_service`` return flat rows. A Report Studio report is a
:class:`RichReport`: headline KPIs (with change versus a comparison period), charts, tables, the key
findings an analyst would call out, and recommended actions. One structure feeds the on-screen
dashboard (JSON), the PDF, the Excel workbook and the CSV, so the four outputs can never disagree.

Report kinds (see :data:`CATALOG`):

    period_completion   task completion for one period (the "monthly task completion report")
    pending_by_member   open work per team member
    period_comparison   one period against the previous one, plus a trend over recent periods
    close_status        close readiness of every entity
    overdue             overdue items, their age and who holds them

Definitions (kept identical to the app so a report never contradicts a screen):

  * The tasks counted for a period are the tasks of that period's plans, MINUS carried-forward tasks
    (they were moved to the next period), upcoming placeholders (a future occurrence) and SKIPPED
    tasks: an optional task (is_required = false) that is past its due date and still not started,
    assigned or in progress. A skipped task never adds to a total, a completed count or a percentage
    and is reported separately as "skipped". Every other optional task counts like a required one.
  * Open = not started, assigned, in progress, pending review, rejected.
  * Overdue = open, not skipped, and due before the evaluation date.
    Its age is counted in working days (weekends and holidays do not age a task).
  * A task is held by its single assignee and/or every entity-wide assignee who has not yet ticked
    their part; a task with nobody on it is "unassigned".
  * The evaluation date is today, or the day after the period ends for a finished period, so a closed
    period is judged as it stood at close.

The module has three layers: dataclasses + pure analysis (no I/O, unit-tested with synthetic data),
a loader (SQL, tenant- and role-scoped), and :func:`build_report` tying them together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import nepal_today, working_days_between
from ..models import (
    Entity,
    Period,
    Plan,
    Task,
    TaskEntityAssignee,
    User,
)

# --------------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------------
KIND_COMPLETION = "period_completion"
KIND_MEMBERS = "pending_by_member"
KIND_COMPARISON = "period_comparison"
KIND_CLOSE_STATUS = "close_status"
KIND_OVERDUE = "overdue"

OPEN_STATUSES = frozenset({"NOT_STARTED", "ASSIGNED", "IN_PROGRESS", "PENDING_REVIEW", "REJECTED"})
EXCLUDED_STATUSES = frozenset({"CARRIED_FORWARD", "UPCOMING"})
# An optional task past its due date in one of these statuses is "skipped" (see optional_tasks.py).
SKIPPABLE_STATUSES = frozenset({"NOT_STARTED", "ASSIGNED", "IN_PROGRESS"})

DUE_SOON_DAYS = 7
MAX_TABLE_ROWS = 1000
TREND_PERIODS = 6
NEPAL_TZ = timezone(timedelta(hours=5, minutes=45))

CATALOG: list[dict[str, Any]] = [
    {
        "kind": KIND_COMPLETION,
        "label": "Task completion report",
        "description": "How much of a period's work is done, by entity, with the trend, overdue items and next steps.",
        "examples": ["Generate the monthly task completion report.", "How is this period going?"],
        "uses_comparison": False,
    },
    {
        "kind": KIND_MEMBERS,
        "label": "Pending tasks by team member",
        "description": "Who holds the open work, who is overloaded, and what is overdue per person.",
        "examples": ["Prepare a report of pending tasks by team member."],
        "uses_comparison": False,
    },
    {
        "kind": KIND_COMPARISON,
        "label": "Period comparison",
        "description": "This period against the previous one: KPI changes, entity movers and a multi-period trend.",
        "examples": ["Compare this period with the previous period."],
        "uses_comparison": True,
    },
    {
        "kind": KIND_CLOSE_STATUS,
        "label": "Close-status report",
        "description": "Close readiness of every entity: closed, ready, on track, at risk or behind.",
        "examples": ["Generate a close-status report for all entities."],
        "uses_comparison": False,
    },
    {
        "kind": KIND_OVERDUE,
        "label": "Overdue items report",
        "description": "Every overdue item with its age in working days, grouped by entity and owner.",
        "examples": ["Show me an overdue report as Excel."],
        "uses_comparison": False,
    },
]
KINDS = tuple(c["kind"] for c in CATALOG)
LABELS = {c["kind"]: c["label"] for c in CATALOG}


class ReportError(Exception):
    """A request the engine cannot satisfy (unknown period, no periods defined, ...)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------------------------
# Output types
# --------------------------------------------------------------------------------------------
@dataclass
class Kpi:
    key: str
    label: str
    value: Optional[float]
    display: str
    unit: str = "count"  # count | percent | days | hours
    tone: str = "neutral"  # good | warn | bad | neutral
    delta: Optional[float] = None  # change versus the comparison period
    delta_display: Optional[str] = None
    delta_tone: str = "neutral"
    hint: str = ""


@dataclass
class Series:
    name: str
    values: list[Optional[float]]
    tone: Optional[str] = None  # good | warn | bad | neutral | info | muted
    point_tones: Optional[list[str]] = None  # one tone per value (donut slices)


@dataclass
class Chart:
    id: str
    title: str
    kind: str  # donut | bar | hbar | stacked_bar | stacked_hbar | line
    labels: list[str]
    series: list[Series]
    unit: str = "count"  # count | percent
    subtitle: str = ""


@dataclass
class Column:
    key: str
    label: str
    type: str = "text"  # text | int | percent | date | days | badge


@dataclass
class Table:
    id: str
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    primary: bool = False  # the table the CSV export contains
    note: str = ""
    total_rows: Optional[int] = None  # set when ``rows`` was truncated


@dataclass
class Finding:
    severity: str  # good | info | warn | bad
    text: str


@dataclass
class Recommendation:
    priority: str  # high | medium | low
    text: str


@dataclass
class RichReport:
    kind: str
    title: str
    subtitle: str
    generated_at: datetime
    scope: dict[str, Any]
    kpis: list[Kpi] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def primary_table(self) -> Optional[Table]:
        return next((t for t in self.tables if t.primary), self.tables[0] if self.tables else None)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


# --------------------------------------------------------------------------------------------
# Input rows (what the loader produces and the analysis consumes)
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PeriodInfo:
    id: str
    label: str
    start_date: date
    end_date: date
    status: str = ""


@dataclass(frozen=True)
class PlanRow:
    id: str
    entity_id: str
    status: str  # OPEN | IN_PROGRESS | CLOSED
    title: str = ""
    owner_id: Optional[str] = None


@dataclass(frozen=True)
class TaskRow:
    id: str
    title: str
    status: str
    entity_id: str
    due_date: Optional[date] = None
    completed_on: Optional[date] = None
    is_required: bool = True
    priority: str = "MEDIUM"
    assignee_ids: tuple[str, ...] = ()
    done_ids: frozenset = frozenset()  # entity-wide assignees who finished their part


@dataclass
class PeriodData:
    period: PeriodInfo
    plans: list[PlanRow]
    tasks: list[TaskRow]

    def eval_date(self, today: date) -> date:
        """Today, or the day after the period ended for a finished period."""
        return min(today, self.period.end_date + timedelta(days=1))


@dataclass
class Names:
    entities: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)

    def entity(self, entity_id: str) -> str:
        return self.entities.get(entity_id) or "Unknown entity"

    def user(self, user_id: str) -> str:
        return self.users.get(user_id) or "Unknown user"


# --------------------------------------------------------------------------------------------
# Pure analysis helpers
# --------------------------------------------------------------------------------------------
def pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def is_skipped(task: TaskRow, as_of: date) -> bool:
    """An optional task past its due date that nobody did: listed, but in no total."""
    return bool(
        not task.is_required and task.due_date is not None and task.due_date < as_of
        and task.status in SKIPPABLE_STATUSES
    )


def counted(tasks: Iterable[TaskRow], as_of: date) -> list[TaskRow]:
    """The tasks that add to totals: not carried forward / upcoming, and not skipped."""
    return [t for t in tasks if t.status not in EXCLUDED_STATUSES and not is_skipped(t, as_of)]


def is_open(task: TaskRow) -> bool:
    return task.status in OPEN_STATUSES


def is_overdue(task: TaskRow, as_of: date) -> bool:
    return bool(
        is_open(task) and not is_skipped(task, as_of)
        and task.due_date is not None and task.due_date < as_of
    )


def overdue_age(task: TaskRow, as_of: date) -> int:
    """Working days since the task fell due (0 over a weekend / holiday gap)."""
    return working_days_between(task.due_date, as_of) if task.due_date else 0


def holders(task: TaskRow) -> list[str]:
    """The people who still have to act on an open task (entity-wide assignees who are done are not)."""
    return [u for u in task.assignee_ids if u not in task.done_ids]


def _on_time(task: TaskRow) -> Optional[bool]:
    if task.status != "COMPLETED" or task.completed_on is None or task.due_date is None:
        return None
    return task.completed_on <= task.due_date


def compute_metrics(tasks: Iterable[TaskRow], as_of: date) -> dict[str, Any]:
    all_tasks = list(tasks)
    rows = counted(all_tasks, as_of)
    open_rows = [t for t in rows if is_open(t)]
    completed = [t for t in rows if t.status == "COMPLETED"]
    overdue = [t for t in open_rows if is_overdue(t, as_of)]
    due_known = [t for t in rows if t.due_date is not None]
    due_so_far = [t for t in due_known if t.due_date < as_of]
    judged = [o for o in (_on_time(t) for t in completed) if o is not None]
    horizon = as_of + timedelta(days=DUE_SOON_DAYS)
    return {
        "total": len(rows),
        "completed": len(completed),
        "open": len(open_rows),
        "not_started": sum(1 for t in open_rows if t.status in ("NOT_STARTED", "ASSIGNED")),
        "in_progress": sum(1 for t in open_rows if t.status == "IN_PROGRESS"),
        "pending_review": sum(1 for t in open_rows if t.status == "PENDING_REVIEW"),
        "rejected": sum(1 for t in open_rows if t.status == "REJECTED"),
        "overdue": len(overdue),
        "due_soon": sum(
            1 for t in open_rows if t.due_date is not None and as_of <= t.due_date <= horizon
        ),
        "unassigned_open": sum(1 for t in open_rows if not t.assignee_ids),
        "skipped": sum(1 for t in all_tasks if is_skipped(t, as_of)),
        "carried_forward": sum(1 for t in all_tasks if t.status == "CARRIED_FORWARD"),
        "upcoming": sum(1 for t in all_tasks if t.status == "UPCOMING"),
        "completion_pct": pct(len(completed), len(rows)),
        # Share of the period's work that was already due by the evaluation date: where completion
        # "should" be. None when no task carries a due date.
        "expected_pct": pct(len(due_so_far), len(rows)) if due_known else None,
        "on_time_pct": pct(sum(judged), len(judged)) if judged else None,
        "on_time_judged": len(judged),
    }


def _fmt_int(v: float) -> str:
    return f"{int(round(v)):,}"


def _fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


def _signed(v: float, unit: str) -> str:
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    mag = abs(v)
    if unit == "percent":
        return f"{sign}{mag:.1f} pts"
    return f"{sign}{_fmt_int(mag)}"


def _kpi(
    key: str, label: str, value: Optional[float], *, unit: str = "count", tone: str = "neutral",
    hint: str = "", prev: Optional[float] = None, higher_is_better: Optional[bool] = None,
) -> Kpi:
    display = _fmt_pct(value) if unit == "percent" else ("n/a" if value is None else _fmt_int(value))
    kpi = Kpi(key=key, label=label, value=value, display=display, unit=unit, tone=tone, hint=hint)
    if prev is not None and value is not None:
        delta = round(value - prev, 1)
        kpi.delta = delta
        kpi.delta_display = _signed(delta, unit)
        if delta == 0 or higher_is_better is None:
            kpi.delta_tone = "neutral"
        else:
            kpi.delta_tone = "good" if (delta > 0) == higher_is_better else "bad"
    return kpi


def completion_tone(completion: float, expected: Optional[float]) -> str:
    if expected is None:
        return "neutral"
    if completion + 5 >= expected:
        return "good"
    if completion + 20 >= expected:
        return "warn"
    return "bad"


def count_tone(n: int, *, warn_at: int = 1, bad_at: int = 10) -> str:
    return "good" if n == 0 else ("warn" if n < bad_at and n >= warn_at else "bad")


def readiness(entity_tasks: list[TaskRow], plans: list[PlanRow], as_of: date) -> str:
    """closed | ready | on_track | at_risk | behind | no_tasks for one entity."""
    if plans and all(p.status == "CLOSED" for p in plans):
        return "closed"
    m = compute_metrics(entity_tasks, as_of)
    if m["total"] == 0:
        return "no_tasks"
    if m["open"] == 0:
        return "ready"
    expected = m["expected_pct"]
    behind_schedule = expected is not None and m["completion_pct"] + 25 < expected
    if m["overdue"] == 0 and (expected is None or m["completion_pct"] + 10 >= expected):
        return "on_track"
    if pct(m["overdue"], m["open"]) >= 25 or behind_schedule:
        return "behind"
    return "at_risk"


READINESS_LABEL = {
    "closed": "Closed", "ready": "Ready to close", "on_track": "On track",
    "at_risk": "At risk", "behind": "Behind", "no_tasks": "No tasks",
}
READINESS_TONE = {
    "closed": "good", "ready": "good", "on_track": "good", "at_risk": "warn", "behind": "bad", "no_tasks": "neutral",
}


def _by_entity(data: PeriodData, as_of: date) -> list[dict[str, Any]]:
    groups: dict[str, list[TaskRow]] = {}
    for p in data.plans:
        groups.setdefault(p.entity_id, [])
    for t in data.tasks:
        groups.setdefault(t.entity_id, []).append(t)
    out = []
    for entity_id, tasks in groups.items():
        plans = [p for p in data.plans if p.entity_id == entity_id]
        m = compute_metrics(tasks, as_of)
        out.append({"entity_id": entity_id, "metrics": m, "plans": plans, "readiness": readiness(tasks, plans, as_of)})
    return out


def _scope(
    data: PeriodData, names: Names, *, entity_id: Optional[str] = None, member_id: Optional[str] = None,
    compare: Optional[PeriodInfo] = None, as_of: Optional[date] = None,
) -> dict[str, Any]:
    return {
        "period": {
            "id": data.period.id, "label": data.period.label,
            "start_date": data.period.start_date, "end_date": data.period.end_date, "status": data.period.status,
        },
        "compare_period": (
            {"id": compare.id, "label": compare.label, "start_date": compare.start_date, "end_date": compare.end_date}
            if compare else None
        ),
        "entity": names.entity(entity_id) if entity_id else "All entities",
        "member": names.user(member_id) if member_id else None,
        "as_of": as_of,
    }


STANDARD_NOTES = [
    "Carried-forward tasks, upcoming placeholders and skipped optional tasks (past due, not done) are excluded from counts and completion rates.",
    "Overdue = open, not skipped, and due before the evaluation date; a skipped optional task is never overdue. "
    "Age is counted in working days (weekends and holidays do not age a task).",
]


# --------------------------------------------------------------------------------------------
# Findings & recommendations (shared)
# --------------------------------------------------------------------------------------------
def _entity_findings(rows: list[dict[str, Any]], names: Names) -> tuple[list[Finding], list[Recommendation]]:
    findings: list[Finding] = []
    recs: list[Recommendation] = []
    live = [r for r in rows if r["metrics"]["total"] > 0]
    if len(live) >= 2:
        best = max(live, key=lambda r: (r["metrics"]["completion_pct"], r["metrics"]["total"]))
        worst = min(live, key=lambda r: (r["metrics"]["completion_pct"], -r["metrics"]["total"]))
        if best["entity_id"] != worst["entity_id"]:
            findings.append(Finding(
                "info",
                f"{names.entity(best['entity_id'])} leads at {_fmt_pct(best['metrics']['completion_pct'])} complete; "
                f"{names.entity(worst['entity_id'])} trails at {_fmt_pct(worst['metrics']['completion_pct'])}.",
            ))
    with_overdue = sorted((r for r in live if r["metrics"]["overdue"] > 0), key=lambda r: -r["metrics"]["overdue"])
    if with_overdue:
        total_overdue = sum(r["metrics"]["overdue"] for r in with_overdue)
        top = with_overdue[0]
        share = pct(top["metrics"]["overdue"], total_overdue)
        if len(with_overdue) > 1 and share >= 50:
            findings.append(Finding(
                "warn",
                f"{names.entity(top['entity_id'])} holds {share:.0f}% of all overdue items "
                f"({top['metrics']['overdue']} of {total_overdue}).",
            ))
        recs.append(Recommendation(
            "high" if top["metrics"]["overdue"] >= 10 else "medium",
            f"Review {names.entity(top['entity_id'])} first: {top['metrics']['overdue']} overdue "
            f"item{'s' if top['metrics']['overdue'] != 1 else ''}. Agree new dates or reassign the owners this week.",
        ))
    return findings, recs


def _core_findings(m: dict[str, Any]) -> tuple[list[Finding], list[Recommendation]]:
    findings: list[Finding] = []
    recs: list[Recommendation] = []
    if m["total"] == 0:
        findings.append(Finding("info", "There are no tasks in scope for this period."))
        return findings, recs

    expected = m["expected_pct"]
    if expected is not None:
        gap = round(m["completion_pct"] - expected, 1)
        if gap >= -5:
            findings.append(Finding(
                "good", f"Completion ({_fmt_pct(m['completion_pct'])}) is on or ahead of the {_fmt_pct(expected)} "
                        f"that was already due."))
        else:
            findings.append(Finding(
                "bad" if gap < -20 else "warn",
                f"Completion ({_fmt_pct(m['completion_pct'])}) is {abs(gap):.1f} points behind the "
                f"{_fmt_pct(expected)} of work that was already due."))
    if m["overdue"]:
        findings.append(Finding(
            "bad" if pct(m["overdue"], m["open"]) >= 25 else "warn",
            f"{_fmt_int(m['overdue'])} required task{'s are' if m['overdue'] != 1 else ' is'} overdue "
            f"({pct(m['overdue'], m['open']):.0f}% of open work)."))
        recs.append(Recommendation(
            "high" if m["overdue"] >= 10 else "medium",
            f"Clear or re-date the {m['overdue']} overdue item{'s' if m['overdue'] != 1 else ''}; "
            f"start with the oldest in the overdue list."))
    else:
        findings.append(Finding("good", "Nothing required is overdue."))
    if m["pending_review"]:
        findings.append(Finding(
            "warn" if m["pending_review"] >= 5 else "info",
            f"{_fmt_int(m['pending_review'])} task{'s are' if m['pending_review'] != 1 else ' is'} waiting for review "
            f"- approving them raises completion by up to {pct(m['pending_review'], m['total']):.1f} points."))
        recs.append(Recommendation(
            "high" if m["pending_review"] >= 10 else "medium",
            f"Review the {m['pending_review']} submitted task{'s' if m['pending_review'] != 1 else ''} "
            f"so finished work counts as complete."))
    if m["rejected"]:
        findings.append(Finding(
            "warn", f"{_fmt_int(m['rejected'])} task{'s were' if m['rejected'] != 1 else ' was'} sent back "
                    f"and still need rework."))
        recs.append(Recommendation("medium", f"Follow up on the {m['rejected']} rejected task(s) with their owners."))
    if m["unassigned_open"]:
        findings.append(Finding(
            "warn", f"{_fmt_int(m['unassigned_open'])} open task{'s have' if m['unassigned_open'] != 1 else ' has'} "
                    f"nobody assigned."))
        recs.append(Recommendation(
            "high" if pct(m["unassigned_open"], m["open"]) >= 25 else "medium",
            f"Assign owners to the {m['unassigned_open']} unassigned open task(s) before they become overdue."))
    if m["on_time_pct"] is not None:
        sev = "good" if m["on_time_pct"] >= 90 else ("warn" if m["on_time_pct"] >= 75 else "bad")
        findings.append(Finding(
            sev, f"{_fmt_pct(m['on_time_pct'])} of completed tasks were finished on or before their due date "
                 f"({m['on_time_judged']} judged)."))
        if m["on_time_pct"] < 75:
            recs.append(Recommendation(
                "medium", "Completed work is landing late: bring due dates forward in planning or add reminders."))
    if m["due_soon"]:
        findings.append(Finding(
            "info", f"{_fmt_int(m['due_soon'])} open task{'s fall' if m['due_soon'] != 1 else ' falls'} "
                    f"due in the next {DUE_SOON_DAYS} days."))
    if m["skipped"]:
        findings.append(Finding(
            "info", f"{_fmt_int(m['skipped'])} optional task{'s were' if m['skipped'] != 1 else ' was'} "
                    f"skipped (past due and not done): not counted in the totals and never overdue."))
    return findings, recs


def _prioritise(recs: list[Recommendation], limit: int = 7) -> list[Recommendation]:
    rank = {"high": 0, "medium": 1, "low": 2}
    seen: set[str] = set()
    out: list[Recommendation] = []
    for r in sorted(recs, key=lambda r: rank.get(r.priority, 3)):
        if r.text in seen:
            continue
        seen.add(r.text)
        out.append(r)
    if not out:
        out.append(Recommendation("low", "No action needed right now - keep the current pace."))
    return out[:limit]


# --------------------------------------------------------------------------------------------
# Chart builders
# --------------------------------------------------------------------------------------------
def _status_chart(m: dict[str, Any]) -> Chart:
    labels = ["Completed", "In progress", "Not started", "Pending review", "Rejected"]
    values = [m["completed"], m["in_progress"], m["not_started"], m["pending_review"], m["rejected"]]
    tones = ["good", "info", "muted", "warn", "bad"]
    keep = [i for i, v in enumerate(values) if v > 0]
    if not keep:
        return _donut_with_tones("status_mix", "Task status mix", [("No tasks", 0.0, "muted")])
    return _donut_with_tones(
        "status_mix", "Task status mix", [(labels[i], float(values[i]), tones[i]) for i in keep],
        subtitle="Share of the period's counted tasks by status",
    )


def _donut_with_tones(chart_id: str, title: str, items: list[tuple[str, float, str]], subtitle: str = "") -> Chart:
    return Chart(
        id=chart_id, title=title, kind="donut",
        labels=[label for label, _v, _t in items],
        series=[Series("Tasks", [v for _l, v, _t in items], point_tones=[t for _l, _v, t in items])],
        subtitle=subtitle,
    )


def _burn_chart(data: PeriodData, as_of: date) -> Optional[Chart]:
    """Cumulative work that has fallen due versus work completed, week by week through the period."""
    rows = counted(data.tasks, as_of)
    if not rows:
        return None
    start, end = data.period.start_date, data.period.end_date
    span = (end - start).days + 1
    step = 7 if span <= 98 else max(7, -(-span // 14))
    ends: list[date] = []
    d = start + timedelta(days=step - 1)
    while d < end:
        ends.append(d)
        d += timedelta(days=step)
    ends.append(end)
    last_known = min(as_of, end)  # completions are known up to today (or to the end of a finished period)
    planned: list[Optional[float]] = []
    done: list[Optional[float]] = []
    previous_end = start - timedelta(days=1)
    for e in ends:
        planned.append(float(sum(1 for t in rows if t.due_date is not None and t.due_date <= e)))
        if previous_end < last_known:  # this bucket has begun; the running one shows progress to date
            cutoff = min(e, last_known)
            done.append(float(sum(
                1 for t in rows if t.status == "COMPLETED" and t.completed_on is not None and t.completed_on <= cutoff
            )))
        else:
            done.append(None)
        previous_end = e
    labels = [e.strftime("%d %b") for e in ends]
    return Chart(
        id="burn_up", title="Progress through the period", kind="line", labels=labels,
        series=[Series("Due (cumulative)", planned, tone="muted"), Series("Completed (cumulative)", done, tone="good")],
        subtitle="Completed work against the work that has fallen due (the running week shows progress to date)",
    )


def _age_bucket(age: int) -> int:
    return 0 if age <= 2 else (1 if age <= 5 else (2 if age <= 10 else 3))


AGE_LABELS = ["0-2 working days", "3-5 working days", "6-10 working days", "11+ working days"]


def _ageing_chart(overdue: list[TaskRow], as_of: date) -> Optional[Chart]:
    if not overdue:
        return None
    counts = [0, 0, 0, 0]
    for t in overdue:
        counts[_age_bucket(overdue_age(t, as_of))] += 1
    return Chart(
        id="overdue_ageing", title="How late are the overdue items?", kind="bar", labels=AGE_LABELS,
        series=[Series("Overdue tasks", [float(c) for c in counts], tone="bad")],
        subtitle="Overdue tasks by age in working days",
    )


def _entity_completion_chart(rows: list[dict[str, Any]], names: Names) -> Optional[Chart]:
    live = [r for r in rows if r["metrics"]["total"] > 0]
    if not live:
        return None
    live.sort(key=lambda r: -r["metrics"]["completion_pct"])
    return Chart(
        id="entity_completion", title="Completion by entity", kind="hbar",
        labels=[names.entity(r["entity_id"]) for r in live],
        series=[Series("Completion %", [r["metrics"]["completion_pct"] for r in live], tone="good")],
        unit="percent", subtitle="Share of each entity's tasks that are complete",
    )


# --------------------------------------------------------------------------------------------
# Report: period completion
# --------------------------------------------------------------------------------------------
def _task_row_dict(t: TaskRow, names: Names, as_of: date) -> dict[str, Any]:
    who = [names.user(u) for u in t.assignee_ids]
    return {
        "title": t.title,
        "entity": names.entity(t.entity_id),
        "assignees": ", ".join(who) if who else "Unassigned",
        "status": t.status.replace("_", " ").title(),
        "priority": t.priority.title() if t.priority else "",
        "due_date": t.due_date,
        "days_overdue": overdue_age(t, as_of) if is_overdue(t, as_of) else 0,
    }


OVERDUE_COLUMNS = [
    Column("title", "Task"), Column("entity", "Entity"), Column("assignees", "Assigned to"),
    Column("status", "Status"), Column("priority", "Priority"), Column("due_date", "Due date", "date"),
    Column("days_overdue", "Working days overdue", "days"),
]


def _overdue_table(overdue: list[TaskRow], names: Names, as_of: date, *, primary: bool = False) -> Table:
    ordered = sorted(overdue, key=lambda t: (-overdue_age(t, as_of), t.due_date or date.max, t.title))
    rows = [_task_row_dict(t, names, as_of) for t in ordered[:MAX_TABLE_ROWS]]
    return Table(
        id="overdue_items", title="Overdue items", columns=OVERDUE_COLUMNS, rows=rows, primary=primary,
        note="Oldest first." + (f" Showing the first {MAX_TABLE_ROWS}." if len(ordered) > MAX_TABLE_ROWS else ""),
        total_rows=len(ordered) if len(ordered) > MAX_TABLE_ROWS else None,
    )


ENTITY_COLUMNS = [
    Column("entity", "Entity"), Column("total", "Tasks", "int"), Column("completed", "Completed", "int"),
    Column("completion_pct", "Completion", "percent"), Column("open", "Open", "int"),
    Column("pending_review", "In review", "int"), Column("overdue", "Overdue", "int"),
    Column("on_time_pct", "On-time", "percent"), Column("readiness", "Readiness", "badge"),
]


def _entity_table(rows: list[dict[str, Any]], names: Names, *, primary: bool = False) -> Table:
    out = []
    for r in sorted(rows, key=lambda r: names.entity(r["entity_id"]).lower()):
        m = r["metrics"]
        out.append({
            "entity": names.entity(r["entity_id"]), "total": m["total"], "completed": m["completed"],
            "completion_pct": m["completion_pct"], "open": m["open"], "pending_review": m["pending_review"],
            "overdue": m["overdue"], "on_time_pct": m["on_time_pct"],
            "readiness": READINESS_LABEL[r["readiness"]],
        })
    return Table(id="by_entity", title="By entity", columns=ENTITY_COLUMNS, rows=out, primary=primary)


def _headline_kpis(m: dict[str, Any], prev: Optional[dict[str, Any]] = None) -> list[Kpi]:
    g = (lambda k: prev[k]) if prev is not None else (lambda k: None)
    return [
        _kpi("completion", "Completion", m["completion_pct"], unit="percent",
             tone=completion_tone(m["completion_pct"], m["expected_pct"]), prev=g("completion_pct"),
             higher_is_better=True,
             hint=f"{m['completed']:,} of {m['total']:,} tasks" + (
                 f"; {_fmt_pct(m['expected_pct'])} was due by now" if m["expected_pct"] is not None else "")),
        _kpi("total", "Tasks in scope", m["total"], prev=g("total")),
        _kpi("open", "Open tasks", m["open"], prev=g("open"), higher_is_better=False),
        _kpi("overdue", "Overdue", m["overdue"], tone=count_tone(m["overdue"]), prev=g("overdue"),
             higher_is_better=False, hint="Required tasks past their due date"),
        _kpi("pending_review", "Waiting for review", m["pending_review"],
             tone="good" if m["pending_review"] == 0 else ("warn" if m["pending_review"] < 10 else "bad"),
             prev=g("pending_review"), higher_is_better=False),
        _kpi("on_time", "On-time rate", m["on_time_pct"], unit="percent",
             tone=("neutral" if m["on_time_pct"] is None else
                   "good" if m["on_time_pct"] >= 90 else "warn" if m["on_time_pct"] >= 75 else "bad"),
             prev=g("on_time_pct"), higher_is_better=True, hint="Completed on or before the due date"),
        _kpi("due_soon", f"Due in {DUE_SOON_DAYS} days", m["due_soon"], hint="Open tasks coming due"),
        _kpi("unassigned", "Unassigned open", m["unassigned_open"], tone=count_tone(m["unassigned_open"]),
             prev=g("unassigned_open"), higher_is_better=False),
    ]


def build_period_completion(data: PeriodData, names: Names, today: date, *, entity_id: Optional[str] = None,
                            generated_at: Optional[datetime] = None) -> RichReport:
    as_of = data.eval_date(today)
    m = compute_metrics(data.tasks, as_of)
    rows = _by_entity(data, as_of)
    overdue = [t for t in counted(data.tasks, as_of) if is_overdue(t, as_of)]

    findings, recs = _core_findings(m)
    f2, r2 = _entity_findings(rows, names)
    findings += f2
    recs += r2

    charts = [c for c in (
        _status_chart(m), _entity_completion_chart(rows, names), _burn_chart(data, as_of), _ageing_chart(overdue, as_of),
    ) if c is not None]
    tables = [_entity_table(rows, names, primary=True)]
    if overdue:
        tables.append(_overdue_table(overdue, names, as_of))
    return RichReport(
        kind=KIND_COMPLETION,
        title=f"Task completion report - {data.period.label}",
        subtitle=f"{data.period.start_date:%d %b %Y} to {data.period.end_date:%d %b %Y}"
                 f" - evaluated {as_of:%d %b %Y} - {names.entity(entity_id) if entity_id else 'all entities'}",
        generated_at=generated_at or datetime.now(timezone.utc),
        scope=_scope(data, names, entity_id=entity_id, as_of=as_of),
        kpis=_headline_kpis(m), charts=charts, tables=tables, findings=findings,
        recommendations=_prioritise(recs), notes=list(STANDARD_NOTES),
    )


# --------------------------------------------------------------------------------------------
# Report: pending by member
# --------------------------------------------------------------------------------------------
MEMBER_COLUMNS = [
    Column("member", "Team member"), Column("to_do", "To do", "int"), Column("in_review", "In review", "int"),
    Column("overdue", "Overdue", "int"), Column("due_soon", f"Due in {DUE_SOON_DAYS} days", "int"),
    Column("oldest_overdue_days", "Oldest overdue (working days)", "days"), Column("completed", "Completed", "int"),
    Column("completion_pct", "Completion", "percent"),
]


def member_rows(data: PeriodData, names: Names, as_of: date, member_id: Optional[str] = None) -> list[dict[str, Any]]:
    """One row per person who holds work, plus an 'Unassigned' row when open work has no owner."""
    acc: dict[str, dict[str, Any]] = {}

    def row(key: str, label: str) -> dict[str, Any]:
        return acc.setdefault(key, {
            "member_id": key, "member": label, "to_do": 0, "in_review": 0, "overdue": 0, "due_soon": 0,
            "oldest_overdue_days": 0, "completed": 0, "assigned_total": 0,
        })

    horizon = as_of + timedelta(days=DUE_SOON_DAYS)
    for t in counted(data.tasks, as_of):
        who = list(t.assignee_ids)
        if not who:
            if is_open(t):
                who_keys = [("__unassigned__", "Unassigned")]
            else:
                continue
        else:
            who_keys = [(u, names.user(u)) for u in who]
        for key, label in who_keys:
            if member_id and key != member_id:
                continue
            r = row(key, label)
            r["assigned_total"] += 1
            if t.status == "COMPLETED" or key in t.done_ids:  # finished, or their part of a shared task is
                r["completed"] += 1
                continue
            if not is_open(t):
                continue
            if t.status == "PENDING_REVIEW":
                r["in_review"] += 1
            else:
                r["to_do"] += 1
            if is_overdue(t, as_of):
                r["overdue"] += 1
                r["oldest_overdue_days"] = max(r["oldest_overdue_days"], overdue_age(t, as_of))
            elif t.due_date is not None and as_of <= t.due_date <= horizon:
                r["due_soon"] += 1
    out = []
    for r in acc.values():
        r["completion_pct"] = pct(r["completed"], r["assigned_total"])
        out.append(r)
    out.sort(key=lambda r: (-(r["to_do"] + r["in_review"]), -r["overdue"], r["member"].lower()))
    return out


def build_pending_by_member(data: PeriodData, names: Names, today: date, *, entity_id: Optional[str] = None,
                            member_id: Optional[str] = None, generated_at: Optional[datetime] = None) -> RichReport:
    as_of = data.eval_date(today)
    m = compute_metrics(data.tasks, as_of)
    rows = member_rows(data, names, as_of, member_id)
    people = [r for r in rows if r["member_id"] != "__unassigned__"]
    holding = [r for r in people if r["to_do"] + r["in_review"] > 0]
    pending_total = sum(r["to_do"] + r["in_review"] for r in holding)

    kpis = [
        _kpi("open", "Open tasks", m["open"], hint="Unique tasks still open"),
        _kpi("holders", "People with pending work", len(holding)),
        _kpi("overdue", "Overdue", m["overdue"], tone=count_tone(m["overdue"]), higher_is_better=False),
        _kpi("pending_review", "Waiting for review", m["pending_review"],
             tone="good" if m["pending_review"] == 0 else "warn"),
        _kpi("due_soon", f"Due in {DUE_SOON_DAYS} days", m["due_soon"]),
        _kpi("unassigned", "Unassigned open", m["unassigned_open"], tone=count_tone(m["unassigned_open"])),
    ]
    findings: list[Finding] = []
    recs: list[Recommendation] = []
    if not holding and not m["unassigned_open"]:
        findings.append(Finding("good", "Nobody has pending work in this scope."))
    if holding:
        top = holding[0]
        top_load = top["to_do"] + top["in_review"]
        share = pct(top_load, pending_total)
        findings.append(Finding(
            "info", f"{top['member']} holds the most pending work: {top_load} task{'s' if top_load != 1 else ''} "
                    f"({share:.0f}% of everything pending)."))
        avg = pending_total / len(holding)
        if len(holding) >= 3 and top_load >= 2 * avg:
            findings.append(Finding(
                "warn", f"{top['member']} carries {top_load / avg:.1f}x the average load of {avg:.1f} tasks."))
            recs.append(Recommendation(
                "high" if top_load >= 20 else "medium",
                f"Rebalance: move some of {top['member']}'s {top_load} pending tasks to people with lighter queues."))
        worst = max(holding, key=lambda r: (r["overdue"], r["oldest_overdue_days"]))
        if worst["overdue"]:
            findings.append(Finding(
                "bad" if worst["overdue"] >= 10 else "warn",
                f"{worst['member']} has the most overdue work: {worst['overdue']} item"
                f"{'s' if worst['overdue'] != 1 else ''}, the oldest {worst['oldest_overdue_days']} working days late."))
            recs.append(Recommendation(
                "high" if worst["overdue"] >= 10 else "medium",
                f"Hold a short check-in with {worst['member']} on the {worst['overdue']} overdue item(s)."))
        idle = [r for r in people if r["to_do"] + r["in_review"] == 0 and r["assigned_total"] > 0]
        if idle and holding:
            findings.append(Finding(
                "info", f"{len(idle)} {'person has' if len(idle) == 1 else 'people have'} no pending work "
                        f"({', '.join(r['member'] for r in idle[:4])}{'...' if len(idle) > 4 else ''}) - spare capacity."))
            recs.append(Recommendation("low", "Offer the spare capacity above to the people with the heaviest queues."))
    if m["unassigned_open"]:
        findings.append(Finding(
            "warn", f"{m['unassigned_open']} open task{'s have' if m['unassigned_open'] != 1 else ' has'} nobody assigned."))
        recs.append(Recommendation("high", f"Assign an owner to the {m['unassigned_open']} unassigned open task(s)."))
    if m["pending_review"]:
        recs.append(Recommendation(
            "medium", f"{m['pending_review']} task(s) are waiting on a reviewer - approving them removes them from the queues."))

    chart_rows = [r for r in rows if r["to_do"] + r["in_review"] > 0]
    charts: list[Chart] = []
    if chart_rows:
        charts.append(Chart(
            id="member_load", title="Pending work per person", kind="stacked_hbar",
            labels=[r["member"] for r in chart_rows],
            series=[
                Series("On track", [float(max(0, r["to_do"] + r["in_review"] - r["overdue"] - r["due_soon"])) for r in chart_rows], tone="good"),
                Series(f"Due in {DUE_SOON_DAYS} days", [float(r["due_soon"]) for r in chart_rows], tone="warn"),
                Series("Overdue", [float(r["overdue"]) for r in chart_rows], tone="bad"),
            ],
            subtitle="Open tasks held by each person, split by urgency",
        ))
        charts.append(Chart(
            id="member_review", title="To do versus in review", kind="stacked_bar",
            labels=[r["member"] for r in chart_rows],
            series=[Series("To do", [float(r["to_do"]) for r in chart_rows], tone="info"),
                    Series("In review", [float(r["in_review"]) for r in chart_rows], tone="warn")],
        ))
    overdue = [t for t in counted(data.tasks, as_of) if is_overdue(t, as_of)
               and (not member_id or member_id in holders(t))]
    ageing = _ageing_chart(overdue, as_of)
    if ageing:
        charts.append(ageing)

    display = [{k: v for k, v in r.items() if k not in ("member_id", "assigned_total")} for r in rows]
    tables = [Table(id="by_member", title="Pending work by team member", columns=MEMBER_COLUMNS, rows=display, primary=True,
                    note="An entity-wide task counts once for every assignee who has not yet ticked their part.")]
    if overdue:
        tables.append(_overdue_table(overdue, names, as_of))
    return RichReport(
        kind=KIND_MEMBERS,
        title=f"Pending tasks by team member - {data.period.label}",
        subtitle=f"{data.period.start_date:%d %b %Y} to {data.period.end_date:%d %b %Y} - evaluated {as_of:%d %b %Y}",
        generated_at=generated_at or datetime.now(timezone.utc),
        scope=_scope(data, names, entity_id=entity_id, member_id=member_id, as_of=as_of),
        kpis=kpis, charts=charts, tables=tables, findings=findings, recommendations=_prioritise(recs),
        notes=list(STANDARD_NOTES),
    )


# --------------------------------------------------------------------------------------------
# Report: overdue
# --------------------------------------------------------------------------------------------
def build_overdue(data: PeriodData, names: Names, today: date, *, entity_id: Optional[str] = None,
                  generated_at: Optional[datetime] = None) -> RichReport:
    as_of = data.eval_date(today)
    m = compute_metrics(data.tasks, as_of)
    overdue = [t for t in counted(data.tasks, as_of) if is_overdue(t, as_of)]
    ages = [overdue_age(t, as_of) for t in overdue]
    by_entity: dict[str, int] = {}
    by_holder: dict[str, int] = {}
    for t in overdue:
        by_entity[t.entity_id] = by_entity.get(t.entity_id, 0) + 1
        for u in (holders(t) or ["__unassigned__"]):
            by_holder[u] = by_holder.get(u, 0) + 1

    avg_age = round(sum(ages) / len(ages), 1) if ages else 0.0
    kpis = [
        _kpi("overdue", "Overdue tasks", len(overdue), tone=count_tone(len(overdue)), higher_is_better=False,
             hint=f"{_fmt_pct(pct(len(overdue), m['open']))} of open work"),
        _kpi("oldest", "Oldest (working days)", max(ages) if ages else 0, unit="days",
             tone="good" if not ages else ("bad" if max(ages) >= 10 else "warn")),
        _kpi("avg_age", "Average age (working days)", avg_age, unit="days"),
        _kpi("entities", "Entities affected", len(by_entity)),
        _kpi("open", "Open tasks", m["open"]),
        _kpi("unassigned", "Overdue with no owner", sum(1 for t in overdue if not t.assignee_ids),
             tone=count_tone(sum(1 for t in overdue if not t.assignee_ids))),
    ]
    findings: list[Finding] = []
    recs: list[Recommendation] = []
    if not overdue:
        findings.append(Finding("good", "No required task is overdue."))
    else:
        findings.append(Finding(
            "bad" if pct(len(overdue), m["open"]) >= 25 else "warn",
            f"{len(overdue)} required task{'s are' if len(overdue) != 1 else ' is'} overdue, "
            f"averaging {avg_age} working days late (oldest {max(ages)})."))
        old = sum(1 for a in ages if a >= 11)
        if old:
            findings.append(Finding("bad", f"{old} item{'s are' if old != 1 else ' is'} more than 10 working days late."))
            recs.append(Recommendation("high", f"Escalate the {old} item(s) that are over 10 working days late."))
        top_e = max(by_entity.items(), key=lambda kv: kv[1])
        findings.append(Finding("info", f"{names.entity(top_e[0])} has the most overdue items ({top_e[1]})."))
        recs.append(Recommendation("medium", f"Start with {names.entity(top_e[0])}: {top_e[1]} overdue item(s)."))
        named = {k: v for k, v in by_holder.items() if k != "__unassigned__"}
        if named:
            top_h = max(named.items(), key=lambda kv: kv[1])
            findings.append(Finding("info", f"{names.user(top_h[0])} holds the most overdue items ({top_h[1]})."))
        ownerless = sum(1 for t in overdue if not t.assignee_ids)
        if ownerless:
            findings.append(Finding("warn", f"{ownerless} overdue item{'s have' if ownerless != 1 else ' has'} no owner."))
            recs.append(Recommendation("high", f"Give the {ownerless} ownerless overdue item(s) an owner today."))
        recs.append(Recommendation("medium", "Re-date items that can no longer be met so due dates stay credible."))

    charts: list[Chart] = []
    ageing = _ageing_chart(overdue, as_of)
    if ageing:
        charts.append(ageing)
    if by_entity:
        order = sorted(by_entity.items(), key=lambda kv: -kv[1])
        charts.append(Chart(id="overdue_entity", title="Overdue by entity", kind="hbar",
                            labels=[names.entity(k) for k, _ in order],
                            series=[Series("Overdue tasks", [float(v) for _, v in order], tone="bad")]))
    if by_holder:
        order = sorted(by_holder.items(), key=lambda kv: -kv[1])[:10]
        charts.append(Chart(id="overdue_owner", title="Overdue by owner (top 10)", kind="hbar",
                            labels=["Unassigned" if k == "__unassigned__" else names.user(k) for k, _ in order],
                            series=[Series("Overdue tasks", [float(v) for _, v in order], tone="bad")]))
    tables = [_overdue_table(overdue, names, as_of, primary=True)]
    return RichReport(
        kind=KIND_OVERDUE, title=f"Overdue items report - {data.period.label}",
        subtitle=f"{data.period.start_date:%d %b %Y} to {data.period.end_date:%d %b %Y} - evaluated {as_of:%d %b %Y}"
                 f" - {names.entity(entity_id) if entity_id else 'all entities'}",
        generated_at=generated_at or datetime.now(timezone.utc),
        scope=_scope(data, names, entity_id=entity_id, as_of=as_of),
        kpis=kpis, charts=charts, tables=tables, findings=findings, recommendations=_prioritise(recs),
        notes=list(STANDARD_NOTES),
    )


# --------------------------------------------------------------------------------------------
# Report: close status
# --------------------------------------------------------------------------------------------
CLOSE_COLUMNS = [
    Column("entity", "Entity"), Column("readiness", "Close status", "badge"), Column("plans", "Plans", "int"),
    Column("plans_closed", "Plans closed", "int"), Column("total", "Tasks", "int"),
    Column("completion_pct", "Completion", "percent"), Column("open", "Open", "int"),
    Column("pending_review", "In review", "int"), Column("overdue", "Overdue", "int"),
    Column("unassigned_open", "Unassigned", "int"), Column("blocker", "What is holding it up"),
]


def _blocker(r: dict[str, Any]) -> str:
    m = r["metrics"]
    if r["readiness"] in ("closed", "no_tasks"):
        return ""
    bits = []
    if m["overdue"]:
        bits.append(f"{m['overdue']} overdue")
    if m["pending_review"]:
        bits.append(f"{m['pending_review']} awaiting review")
    if m["rejected"]:
        bits.append(f"{m['rejected']} rejected")
    if m["unassigned_open"]:
        bits.append(f"{m['unassigned_open']} unassigned")
    rest = m["open"] - m["overdue"] - m["pending_review"] - m["rejected"]
    if rest > 0 and not bits:
        bits.append(f"{m['open']} still open")
    return ", ".join(bits) if bits else ("All tasks done - plans can be closed" if r["readiness"] == "ready" else "")


def build_close_status(data: PeriodData, names: Names, today: date, *, entity_id: Optional[str] = None,
                       generated_at: Optional[datetime] = None) -> RichReport:
    as_of = data.eval_date(today)
    m = compute_metrics(data.tasks, as_of)
    rows = _by_entity(data, as_of)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["readiness"]] = counts.get(r["readiness"], 0) + 1
    n = len(rows)
    ready_n = counts.get("closed", 0) + counts.get("ready", 0)

    kpis = [
        _kpi("entities", "Entities", n),
        _kpi("ready", "Closed or ready to close", ready_n, tone="good" if n and ready_n == n else "neutral",
             hint=f"{pct(ready_n, n):.0f}% of entities"),
        _kpi("on_track", "On track", counts.get("on_track", 0), tone="good"),
        _kpi("at_risk", "At risk", counts.get("at_risk", 0), tone=count_tone(counts.get("at_risk", 0), bad_at=99)),
        _kpi("behind", "Behind", counts.get("behind", 0), tone=count_tone(counts.get("behind", 0), warn_at=0, bad_at=1)),
        _kpi("completion", "Overall completion", m["completion_pct"], unit="percent",
             tone=completion_tone(m["completion_pct"], m["expected_pct"])),
        _kpi("overdue", "Overdue", m["overdue"], tone=count_tone(m["overdue"])),
    ]
    findings: list[Finding] = []
    recs: list[Recommendation] = []
    if n == 0:
        findings.append(Finding("info", "There are no entities with plans in this period."))
    else:
        findings.append(Finding(
            "good" if ready_n == n else "info",
            f"{ready_n} of {n} entit{'y is' if n == 1 else 'ies are'} closed or ready to close."))
        for key, sev in (("behind", "bad"), ("at_risk", "warn")):
            hit = [r for r in rows if r["readiness"] == key]
            if hit:
                findings.append(Finding(
                    sev, f"{', '.join(names.entity(r['entity_id']) for r in hit)} "
                         f"{'is' if len(hit) == 1 else 'are'} {READINESS_LABEL[key].lower()}."))
        for r in rows:
            if r["readiness"] == "ready":
                recs.append(Recommendation(
                    "medium", f"{names.entity(r['entity_id'])} has every task done - close its plan."))
        for r in sorted((r for r in rows if r["readiness"] in ("behind", "at_risk")), key=lambda r: -r["metrics"]["overdue"]):
            recs.append(Recommendation(
                "high" if r["readiness"] == "behind" else "medium",
                f"Unblock {names.entity(r['entity_id'])}: {_blocker(r) or 'review open tasks'}."))
    f2, r2 = _core_findings(m)
    findings += [f for f in f2 if f.severity in ("bad", "warn")]
    recs += [r for r in r2 if r.priority == "high"]

    charts: list[Chart] = []
    if n:
        order = ["closed", "ready", "on_track", "at_risk", "behind", "no_tasks"]
        tones = {"closed": "good", "ready": "good", "on_track": "info", "at_risk": "warn", "behind": "bad", "no_tasks": "muted"}
        items = [(READINESS_LABEL[k], float(counts[k]), tones[k]) for k in order if counts.get(k)]
        charts.append(_donut_with_tones("readiness_mix", "Close readiness", items))
        c = _entity_completion_chart(rows, names)
        if c:
            charts.append(c)
        live = [r for r in rows if r["metrics"]["total"] > 0]
        if live:
            charts.append(Chart(
                id="entity_open_split", title="Open work by entity", kind="stacked_hbar",
                labels=[names.entity(r["entity_id"]) for r in live],
                series=[
                    Series("Other open", [float(max(0, r["metrics"]["open"] - r["metrics"]["overdue"] - r["metrics"]["pending_review"])) for r in live], tone="info"),
                    Series("In review", [float(r["metrics"]["pending_review"]) for r in live], tone="warn"),
                    Series("Overdue", [float(r["metrics"]["overdue"]) for r in live], tone="bad"),
                ]))
    table_rows = []
    for r in sorted(rows, key=lambda r: (["behind", "at_risk", "on_track", "ready", "closed", "no_tasks"].index(r["readiness"]),
                                         names.entity(r["entity_id"]).lower())):
        mm = r["metrics"]
        table_rows.append({
            "entity": names.entity(r["entity_id"]), "readiness": READINESS_LABEL[r["readiness"]],
            "plans": len(r["plans"]), "plans_closed": sum(1 for p in r["plans"] if p.status == "CLOSED"),
            "total": mm["total"], "completion_pct": mm["completion_pct"], "open": mm["open"],
            "pending_review": mm["pending_review"], "overdue": mm["overdue"],
            "unassigned_open": mm["unassigned_open"], "blocker": _blocker(r),
        })
    notes = list(STANDARD_NOTES) + [
        "Close status: Closed = every plan closed; Ready = all tasks done; On track = nothing overdue and completion "
        "within 10 points of the work already due; Behind = 25%+ of open work overdue or 25+ points behind; "
        "At risk = anything in between."]
    return RichReport(
        kind=KIND_CLOSE_STATUS, title=f"Close-status report - {data.period.label}",
        subtitle=f"{data.period.start_date:%d %b %Y} to {data.period.end_date:%d %b %Y} - evaluated {as_of:%d %b %Y}"
                 f" - {names.entity(entity_id) if entity_id else 'all entities'}",
        generated_at=generated_at or datetime.now(timezone.utc),
        scope=_scope(data, names, entity_id=entity_id, as_of=as_of),
        kpis=kpis, charts=charts,
        tables=[Table(id="close_status", title="Close status by entity", columns=CLOSE_COLUMNS, rows=table_rows, primary=True)],
        findings=findings, recommendations=_prioritise(recs), notes=notes,
    )


# --------------------------------------------------------------------------------------------
# Report: period comparison
# --------------------------------------------------------------------------------------------
def build_comparison(cur: PeriodData, prev: PeriodData, history: list[PeriodData], names: Names, today: date, *,
                     entity_id: Optional[str] = None, generated_at: Optional[datetime] = None) -> RichReport:
    cur_as_of, prev_as_of = cur.eval_date(today), prev.eval_date(today)
    cm, pm = compute_metrics(cur.tasks, cur_as_of), compute_metrics(prev.tasks, prev_as_of)

    kpis = _headline_kpis(cm, pm)[:6]
    prev_key = {"completion": "completion_pct", "on_time": "on_time_pct"}
    for k in kpis:
        before = pm.get(prev_key.get(k.key, k.key))
        k.hint = f"{prev.period.label}: " + (_fmt_pct(before) if k.unit == "percent" else _fmt_int(before or 0))

    findings: list[Finding] = []
    recs: list[Recommendation] = []
    if pm["total"] == 0:
        findings.append(Finding("info", f"{prev.period.label} has no tasks in scope, so there is nothing to compare against."))
    else:
        d = round(cm["completion_pct"] - pm["completion_pct"], 1)
        verdict = "good" if d > 1 else ("bad" if d < -1 else "info")
        word = "up" if d > 0 else ("down" if d < 0 else "level")
        findings.append(Finding(
            verdict, f"Completion is {word} {abs(d):.1f} points: {_fmt_pct(cm['completion_pct'])} in {cur.period.label} "
                     f"against {_fmt_pct(pm['completion_pct'])} in {prev.period.label}."))
        od = cm["overdue"] - pm["overdue"]
        findings.append(Finding(
            "bad" if od > 0 else ("good" if od < 0 else "info"),
            f"Overdue items {'rose' if od > 0 else 'fell' if od < 0 else 'are unchanged'}"
            f"{'' if od == 0 else f' by {abs(od)}'}: {cm['overdue']} versus {pm['overdue']}."))
        if cm["on_time_pct"] is not None and pm["on_time_pct"] is not None:
            ot = round(cm["on_time_pct"] - pm["on_time_pct"], 1)
            if abs(ot) >= 1:
                findings.append(Finding("good" if ot > 0 else "warn",
                                        f"On-time completion {'improved' if ot > 0 else 'slipped'} by {abs(ot):.1f} points."))
        if cur.period.status != "CLOSED" and cur_as_of < cur.period.end_date + timedelta(days=1):
            findings.append(Finding(
                "info", f"{cur.period.label} is still running, so its figures will keep moving; {prev.period.label} is final."))

    cur_rows = {r["entity_id"]: r for r in _by_entity(cur, cur_as_of)}
    prev_rows = {r["entity_id"]: r for r in _by_entity(prev, prev_as_of)}
    ents = sorted(set(cur_rows) | set(prev_rows), key=lambda e: names.entity(e).lower())
    table_rows = []
    movers: list[tuple[str, float]] = []
    for e in ents:
        c = cur_rows.get(e, {}).get("metrics") or compute_metrics([], cur_as_of)
        p = prev_rows.get(e, {}).get("metrics") or compute_metrics([], prev_as_of)
        delta = round(c["completion_pct"] - p["completion_pct"], 1) if c["total"] and p["total"] else None
        if delta is not None:
            movers.append((e, delta))
        table_rows.append({
            "entity": names.entity(e), "prev_completion": p["completion_pct"] if p["total"] else None,
            "cur_completion": c["completion_pct"] if c["total"] else None, "delta": delta,
            "prev_overdue": p["overdue"], "cur_overdue": c["overdue"], "delta_overdue": c["overdue"] - p["overdue"],
        })
    if movers:
        up = max(movers, key=lambda kv: kv[1])
        down = min(movers, key=lambda kv: kv[1])
        if up[1] > 1:
            findings.append(Finding("good", f"{names.entity(up[0])} improved most: +{up[1]:.1f} points."))
        if down[1] < -1:
            findings.append(Finding("bad", f"{names.entity(down[0])} slipped most: {down[1]:.1f} points."))
            recs.append(Recommendation(
                "high" if down[1] <= -10 else "medium",
                f"Find out what changed at {names.entity(down[0])} since {prev.period.label} (down {abs(down[1]):.1f} points)."))
    f2, r2 = _core_findings(cm)
    findings += [f for f in f2 if f.severity in ("bad", "warn")][:3]
    recs += r2

    metric_labels = ["Tasks", "Completed", "Open", "Overdue", "In review"]
    keys = ["total", "completed", "open", "overdue", "pending_review"]
    charts = [Chart(
        id="volume_compare", title="Task volumes, side by side", kind="bar", labels=metric_labels,
        series=[Series(prev.period.label, [float(pm[k]) for k in keys], tone="muted"),
                Series(cur.period.label, [float(cm[k]) for k in keys], tone="info")])]
    both = [e for e in ents if e in cur_rows and e in prev_rows
            and cur_rows[e]["metrics"]["total"] and prev_rows[e]["metrics"]["total"]]
    if both:
        charts.append(Chart(
            id="entity_compare", title="Completion by entity", kind="bar", unit="percent",
            labels=[names.entity(e) for e in both],
            series=[Series(prev.period.label, [prev_rows[e]["metrics"]["completion_pct"] for e in both], tone="muted"),
                    Series(cur.period.label, [cur_rows[e]["metrics"]["completion_pct"] for e in both], tone="info")]))
    hist = [h for h in history if h.tasks][-TREND_PERIODS:]
    if len(hist) >= 2:
        hm = [compute_metrics(h.tasks, h.eval_date(today)) for h in hist]
        charts.append(Chart(
            id="trend", title=f"Trend over the last {len(hist)} periods", kind="line", unit="percent",
            labels=[h.period.label for h in hist],
            series=[Series("Completion %", [m["completion_pct"] for m in hm], tone="info"),
                    Series("On-time %", [m["on_time_pct"] for m in hm], tone="good")],
            subtitle="Completion and on-time rate at each period's evaluation date"))
        charts.append(Chart(
            id="trend_overdue", title="Overdue items by period", kind="bar",
            labels=[h.period.label for h in hist],
            series=[Series("Overdue", [float(m["overdue"]) for m in hm], tone="bad")]))
        if len(hm) >= 3:
            last3 = [m["completion_pct"] for m in hm[-3:]]
            if last3[0] < last3[1] < last3[2]:
                findings.append(Finding("good", "Completion has risen three periods in a row."))
            elif last3[0] > last3[1] > last3[2]:
                findings.append(Finding("bad", "Completion has fallen three periods in a row."))
                recs.append(Recommendation("high", "Completion is on a three-period decline: review workload and planning."))

    def kv(label: str, key: str, unit: str = "count") -> dict[str, Any]:
        p, c = pm.get(key), cm.get(key)
        d = round(c - p, 1) if (p is not None and c is not None) else None
        return {"metric": label, "previous": p, "current": c, "change": d}
    kpi_rows = [kv("Tasks in scope", "total"), kv("Completed", "completed"), kv("Completion %", "completion_pct"),
                kv("Open", "open"), kv("Overdue", "overdue"), kv("Waiting for review", "pending_review"),
                kv("On-time %", "on_time_pct"), kv("Unassigned open", "unassigned_open")]
    tables = [
        Table(id="kpi_compare", title="Key figures", primary=True, rows=kpi_rows, columns=[
            Column("metric", "Metric"), Column("previous", prev.period.label, "number"),
            Column("current", cur.period.label, "number"), Column("change", "Change", "number")]),
        Table(id="entity_compare_table", title="By entity", rows=table_rows, columns=[
            Column("entity", "Entity"), Column("prev_completion", f"{prev.period.label} completion", "percent"),
            Column("cur_completion", f"{cur.period.label} completion", "percent"), Column("delta", "Change (pts)", "number"),
            Column("prev_overdue", f"{prev.period.label} overdue", "int"), Column("cur_overdue", f"{cur.period.label} overdue", "int"),
            Column("delta_overdue", "Change", "number")]),
    ]
    return RichReport(
        kind=KIND_COMPARISON, title=f"Period comparison - {cur.period.label} vs {prev.period.label}",
        subtitle=f"{cur.period.start_date:%d %b} - {cur.period.end_date:%d %b %Y} against "
                 f"{prev.period.start_date:%d %b} - {prev.period.end_date:%d %b %Y}"
                 f" - {names.entity(entity_id) if entity_id else 'all entities'}",
        generated_at=generated_at or datetime.now(timezone.utc),
        scope=_scope(cur, names, entity_id=entity_id, compare=prev.period, as_of=cur_as_of),
        kpis=kpis, charts=charts, tables=tables, findings=findings, recommendations=_prioritise(recs),
        notes=list(STANDARD_NOTES) + ["Each period is judged at its own evaluation date: a finished period as it stood at close."],
    )


# --------------------------------------------------------------------------------------------
# Request + loading (SQL)
# --------------------------------------------------------------------------------------------
@dataclass
class ReportSpec:
    kind: str
    period_id: Optional[str] = None  # default: the period covering today
    compare_period_id: Optional[str] = None  # period_comparison only; default: the previous period
    entity_id: Optional[str] = None
    member_id: Optional[str] = None  # pending_by_member only


_CHUNK = 900


def _chunks(ids: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(ids), _CHUNK):
        yield ids[i : i + _CHUNK]


def _nepal_date(moment: Any) -> Optional[date]:
    if moment is None:
        return None
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(NEPAL_TZ).date()
    return moment


def _info(p: Period) -> PeriodInfo:
    return PeriodInfo(id=p.id, label=p.label, start_date=p.start_date, end_date=p.end_date,
                      status=getattr(p.status, "value", str(p.status)))


async def resolve_period(db: AsyncSession, tenant_id: str, period_id: Optional[str], today: date) -> Period:
    if period_id:
        found = (await db.execute(
            select(Period).where(Period.tenant_id == tenant_id, Period.id == period_id)
        )).scalar_one_or_none()
        if found is None:
            raise ReportError("Period not found.", 404)
        return found
    covering = (await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.start_date <= today, Period.end_date >= today)
        .order_by(Period.start_date.desc()).limit(1)
    )).scalar_one_or_none()
    if covering is not None:
        return covering
    latest_past = (await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.end_date < today)
        .order_by(Period.end_date.desc()).limit(1)
    )).scalar_one_or_none()
    if latest_past is not None:
        return latest_past
    first = (await db.execute(
        select(Period).where(Period.tenant_id == tenant_id).order_by(Period.start_date).limit(1)
    )).scalar_one_or_none()
    if first is None:
        raise ReportError("No periods are defined yet, so there is nothing to report on.", 404)
    return first


async def previous_period(db: AsyncSession, tenant_id: str, period: Period) -> Optional[Period]:
    return (await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.end_date < period.start_date)
        .order_by(Period.end_date.desc()).limit(1)
    )).scalar_one_or_none()


async def recent_periods(db: AsyncSession, tenant_id: str, period: Period, count: int = TREND_PERIODS) -> list[Period]:
    rows = (await db.execute(
        select(Period).where(Period.tenant_id == tenant_id, Period.start_date <= period.start_date)
        .order_by(Period.start_date.desc()).limit(count)
    )).scalars().all()
    return list(reversed(rows))


async def load_period_data(
    db: AsyncSession, tenant_id: str, period: Period, entity_scope: Optional[set[str]], entity_id: Optional[str] = None,
) -> PeriodData:
    """The period's plans and tasks the caller may see, as plain rows."""
    info = _info(period)
    allowed = entity_scope
    if entity_id:
        allowed = {entity_id} if allowed is None else (allowed & {entity_id})
    if allowed is not None and not allowed:
        return PeriodData(info, [], [])

    plan_q = select(Plan).where(Plan.tenant_id == tenant_id, Plan.period_id == period.id)
    if allowed is not None:
        plan_q = plan_q.where(Plan.entity_id.in_(allowed))
    plans = list((await db.execute(plan_q)).scalars().all())
    plan_rows = [
        PlanRow(id=p.id, entity_id=p.entity_id, status=getattr(p.status, "value", str(p.status)),
                title=p.title or "", owner_id=p.owner_id)
        for p in plans
    ]
    plan_ids = [p.id for p in plans]

    tasks: list[Task] = []
    for chunk in _chunks(plan_ids):
        tasks += list((await db.execute(
            select(Task).where(Task.tenant_id == tenant_id, Task.plan_id.in_(chunk))
        )).scalars().all())

    shared: dict[str, list[tuple[str, bool]]] = {}
    task_ids = [t.id for t in tasks]
    for chunk in _chunks(task_ids):
        for task_id, user_id, done in (await db.execute(
            select(TaskEntityAssignee.task_id, TaskEntityAssignee.user_id, TaskEntityAssignee.done)
            .where(TaskEntityAssignee.tenant_id == tenant_id, TaskEntityAssignee.task_id.in_(chunk))
        )).all():
            shared.setdefault(task_id, []).append((user_id, bool(done)))

    rows: list[TaskRow] = []
    for t in tasks:
        people: list[str] = []
        if t.member_assigned_to_id:
            people.append(t.member_assigned_to_id)
        done_ids = set()
        for user_id, done in shared.get(t.id, []):
            if user_id not in people:
                people.append(user_id)
            if done:
                done_ids.add(user_id)
        status = getattr(t.status, "value", str(t.status))
        completed_on = None
        if status == "COMPLETED":
            completed_on = _nepal_date(getattr(t, "completed_at", None) or t.updated_at)
        rows.append(TaskRow(
            id=t.id, title=t.title, status=status, entity_id=t.entity_id, due_date=t.due_date,
            completed_on=completed_on, is_required=bool(t.is_required),
            priority=getattr(t.priority, "value", str(t.priority or "")),
            assignee_ids=tuple(people), done_ids=frozenset(done_ids),
        ))
    return PeriodData(info, plan_rows, rows)


async def load_names(db: AsyncSession, tenant_id: str, datas: Iterable[PeriodData], extra_users: Iterable[str] = (),
                     extra_entities: Iterable[str] = ()) -> Names:
    entity_ids = set(extra_entities)
    user_ids = set(extra_users)
    for d in datas:
        entity_ids |= {p.entity_id for p in d.plans} | {t.entity_id for t in d.tasks}
        for t in d.tasks:
            user_ids |= set(t.assignee_ids)
    names = Names()
    for chunk in _chunks(sorted(entity_ids)):
        for eid, name in (await db.execute(
            select(Entity.id, Entity.name).where(Entity.tenant_id == tenant_id, Entity.id.in_(chunk))
        )).all():
            names.entities[eid] = name
    for chunk in _chunks(sorted(user_ids)):
        for uid, name in (await db.execute(
            select(User.id, User.name).where(User.tenant_id == tenant_id, User.id.in_(chunk))
        )).all():
            names.users[uid] = name
    return names


async def build_report(
    db: AsyncSession, *, me: User, spec: ReportSpec, today: Optional[date] = None, tenant_name: Optional[str] = None,
) -> RichReport:
    """Run one report for ``me``. Role and entity scope are enforced here, not left to the caller."""
    from . import report_access_service as access

    access.ensure_can_run_report(me, access.REPORT_STUDIO)
    if spec.kind not in KINDS:
        raise ReportError(f"Unknown report kind '{spec.kind}'.")
    tenant_id = me.tenant_id or ""
    scope = await access.resolve_report_entity_scope(db, me)
    if spec.entity_id:
        await access.ensure_can_view_entity(db, me, spec.entity_id)
    today = today or nepal_today()

    period = await resolve_period(db, tenant_id, spec.period_id, today)
    cur = await load_period_data(db, tenant_id, period, scope, spec.entity_id)
    extra_users = [spec.member_id] if spec.member_id else []
    generated_at = datetime.now(timezone.utc)

    if spec.kind == KIND_COMPARISON:
        if spec.compare_period_id:
            prev_row = await resolve_period(db, tenant_id, spec.compare_period_id, today)
            if prev_row.id == period.id:
                raise ReportError("Choose two different periods to compare.")
        else:
            prev_row = await previous_period(db, tenant_id, period)
        if prev_row is None:
            raise ReportError(
                f"There is no period before {period.label} to compare it with.", 404)
        prev = await load_period_data(db, tenant_id, prev_row, scope, spec.entity_id)
        history = []
        for p in await recent_periods(db, tenant_id, period):
            history.append(cur if p.id == period.id else prev if p.id == prev_row.id
                           else await load_period_data(db, tenant_id, p, scope, spec.entity_id))
        names = await load_names(db, tenant_id, [cur, prev], extra_users, [spec.entity_id] if spec.entity_id else [])
        report = build_comparison(cur, prev, history, names, today, entity_id=spec.entity_id, generated_at=generated_at)
    else:
        names = await load_names(db, tenant_id, [cur], extra_users, [spec.entity_id] if spec.entity_id else [])
        if spec.kind == KIND_COMPLETION:
            report = build_period_completion(cur, names, today, entity_id=spec.entity_id, generated_at=generated_at)
        elif spec.kind == KIND_MEMBERS:
            report = build_pending_by_member(cur, names, today, entity_id=spec.entity_id,
                                             member_id=spec.member_id, generated_at=generated_at)
        elif spec.kind == KIND_CLOSE_STATUS:
            report = build_close_status(cur, names, today, entity_id=spec.entity_id, generated_at=generated_at)
        else:
            report = build_overdue(cur, names, today, entity_id=spec.entity_id, generated_at=generated_at)
    report.scope["tenant"] = tenant_name
    report.scope["generated_by"] = me.name
    return report
