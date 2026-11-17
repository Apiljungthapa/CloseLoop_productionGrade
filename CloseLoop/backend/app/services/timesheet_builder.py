"""Turn the tasks a person SUBMITTED in a month into HRMS timesheet rows.

Pure functions - the caller loads the rows and the HRMS data - so the grouping,
summaries and duplicate rules are unit tested without a database.

Rules that matter (each has a test):

  * A person "worked" on a task on the day they SUBMITTED it, and that is the only
    thing that makes a row: one row per submitted task / sub task (`Work`). The time-log
    table is never read.
  * The hours are the Time Taken entered at submission. If it was left blank the row has
    0 hours and `needs_hours`: the user types them; it is never pre-selected and cannot be
    sent until it has hours. Nothing is invented.
  * An activity with sub activities needs a VALUE for each of them (they are extra
    fields of that activity, not alternatives); a row is only ready once every one has a
    value. Fields named like a description are pre-filled from the summary.
  * The HRMS takes one request per DAY, so a date already sent from CloseLoop is closed:
    later rows on that date are flagged and not selected.
  * Rows already on the HRMS (if it can be read) or already sent from CloseLoop are
    flagged and not selected.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping, Optional, Sequence

from .hrms_timesheet_gateway import ExistingEntry
from .timesheet_matching import (
    normalize,
    AMBIGUOUS,
    MATCHED,
    NEEDS_SUB,
    UNMATCHED,
    Activity,
    Candidate,
    Decision,
    Mappings,
    WorkText,
    decide,
)

WORK_TYPE_DEFAULT = "BILLABLE"
WORK_TYPES = ("BILLABLE", "NON_BILLABLE", "PAID_NON_BILLABLE")
DESC_CLIP = 300  # per sub task / task description in a summary
MINUTES_PER_DAY = 24 * 60

# Flags (strings the UI turns into badges)
ALREADY_IN_HRMS = "ALREADY_IN_HRMS"
ALREADY_SENT = "ALREADY_SENT_FROM_CLOSELOOP"
DAY_ALREADY_SENT = "DAY_ALREADY_SENT_FROM_CLOSELOOP"
OTHER_HRMS_ENTRIES = "HRMS_HAS_OTHER_ENTRIES_THAT_DAY"
DAY_OVER_24H = "DAY_OVER_24_HOURS"
SUMMARY_CLIPPED = "SUMMARY_SHORTENED"

_TAG = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------- data


@dataclass(frozen=True)
class Item:
    """A task or a sub task."""

    id: str
    kind: str  # "task" | "subtask"
    title: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    parent_id: Optional[str] = None  # a sub task's task


@dataclass(frozen=True)
class Work:
    """One task / sub task the user SUBMITTED, on the day they submitted it."""

    unit_id: str            # an Item id (task or sub task)
    work_date: date
    minutes: int            # the Time Taken entered at submission; 0 = none was entered


@dataclass(frozen=True)
class Source:
    """The submission a row files - what the database records as spoken for."""

    unit_kind: str          # "task" | "subtask"
    unit_id: str
    minutes: int
    task_title: str         # for display
    subtask_title: Optional[str] = None


@dataclass
class Line:
    key: str
    work_date: date
    minutes: int
    status: str
    tier: Optional[str] = None
    activity_id: Optional[int] = None
    # sub activity id -> value, for an activity that has sub activities ("" = still to fill in)
    sub_values: dict[int, str] = field(default_factory=dict)
    score: float = 0.0
    reason: str = ""
    suggestions: tuple[Candidate, ...] = ()
    summary: str = ""
    work_type: str = WORK_TYPE_DEFAULT
    sources: list[Source] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    selected: bool = False
    existing_hours: Optional[float] = None
    # The task / sub-task wordings that put time into this line - what a
    # confirmed activity choice is remembered against.
    units: list[WorkText] = field(default_factory=list)
    # Time Taken was left blank at submission: 0 minutes until the user types the hours.
    needs_hours: bool = False

    @property
    def hours(self) -> float:
        return hours_from_minutes(self.minutes)


# ---------------------------------------------------------------- hours


def hours_from_minutes(minutes: int) -> float:
    """Hours to 2 decimals, rounded half up. Always call it on a SUM of
    minutes, never add already-rounded hours."""
    return float((Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"), ROUND_HALF_UP))


# ---------------------------------------------------------------- summary


def _clean(text: Optional[str]) -> str:
    return " ".join(_TAG.sub(" ", text or "").split())


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Shorten at a word boundary with an ellipsis. (text, was_shortened)."""
    if len(text) <= limit:
        return text, False
    cut = text[: max(0, limit - 1)]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…", True


def _desc(item: Item) -> str:
    return clip(_clean(item.description), DESC_CLIP)[0]


def build_summary(
    contribs: Sequence[tuple[Item, Optional[Item]]], max_chars: int
) -> tuple[str, bool]:
    """The text sent with one HRMS entry.

    contribs: (unit, parent task | None) for every unit that put time into the
    line. A task whose time came through sub tasks lists just those sub tasks -
    name and description - under the task's title; a task with its own time and
    no sub task time uses its own description (title if it has none). Joined
    with " | ". Returns (text, was_shortened)."""
    roots: dict[str, dict] = {}
    for unit, parent in contribs:
        root = parent if (unit.kind == "subtask" and parent is not None) else unit
        slot = roots.setdefault(root.id, {"root": root, "subs": {}, "own": False})
        if unit.kind == "subtask":
            slot["subs"][unit.id] = unit
        else:
            slot["own"] = True

    parts: list[str] = []
    for slot in sorted(roots.values(), key=lambda s: s["root"].title.lower()):
        root: Item = slot["root"]
        subs = sorted(slot["subs"].values(), key=lambda u: u.title.lower())
        if subs:
            listed = "; ".join(
                f"{_clean(s.title)} – {_desc(s)}" if _desc(s) else _clean(s.title)
                for s in subs
            )
            parts.append(f"{_clean(root.title)}: {listed}")
        else:
            parts.append(_desc(root) or _clean(root.title))

    seen: set[str] = set()
    unique = [p for p in parts if p and not (p in seen or seen.add(p))]
    return clip(" | ".join(unique), max_chars)


# ---------------------------------------------------------------- lines


_DESCRIPTION_LIKE = frozenset({
    "description", "summary", "details", "detail", "remarks", "remark", "notes", "note", "comment", "comments",
})


def prefill_sub_values(act: Activity, summary: str) -> dict[int, str]:
    """A value for each sub activity of `act`: the summary for fields that are
    obviously a description, "" (to be filled in by the user) for the rest."""
    out: dict[int, str] = {}
    for sub in act.subs:
        like = normalize(sub.name) in _DESCRIPTION_LIKE or normalize(sub.key) in _DESCRIPTION_LIKE
        out[sub.id] = summary if like else ""
    return out


def values_complete(act: Optional[Activity], values: Mapping[int, str]) -> bool:
    """Every sub activity of the activity has a non-blank value (trivially true
    for an activity without sub activities)."""
    return act is not None and all((values.get(sub.id) or "").strip() for sub in act.subs)


def _line_key(work_date: date, entry_ids: Iterable[str]) -> str:
    raw = f"{work_date.isoformat()}|{','.join(sorted(entry_ids))}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _work_text(unit: Item, parent: Optional[Item]) -> WorkText:
    return WorkText(
        title=unit.title,
        description=unit.description,
        template_id=unit.template_id,
        parent_title=parent.title if parent else None,
        parent_template_id=parent.template_id if parent else None,
    )


def work_texts(work: Sequence[Work], items: Mapping[str, Item]) -> list[WorkText]:
    """The distinct task / sub-task wordings behind this work - what remembered
    activity choices are looked up by."""
    out = []
    for w in work:
        unit = items.get(w.unit_id)
        if unit is not None:
            out.append(_work_text(unit, items.get(unit.parent_id or "")))
    return list(dict.fromkeys(out))


def _apply_activity_fields(line: Line, acts_by_id: Mapping[int, Activity], summary: str) -> None:
    """An activity with sub activities needs a value for each: pre-fill what can be
    and hold the line back (NEEDS_SUB) while any is empty."""
    act = acts_by_id.get(line.activity_id) if line.status == MATCHED else None
    if act is not None and act.subs:
        line.sub_values = prefill_sub_values(act, summary)
        if not values_complete(act, line.sub_values):
            line.status = NEEDS_SUB
            line.reason = "activity found - fill in its extra fields: " + ", ".join(
                s_.name for s_ in act.subs if not (line.sub_values.get(s_.id) or "").strip()
            )


def build_lines(
    work: Sequence[Work],
    items: Mapping[str, Item],
    activities: Sequence[Activity],
    mappings: Optional[Mappings] = None,
    *,
    max_summary_chars: int = 1000,
) -> list[Line]:
    """One row per submitted task / sub task. Rows are never merged here (the user may
    need to type hours on one); the ones the user puts on the same activity and date
    are merged into a single HRMS entry when sent (merge_chosen)."""
    acts_by_id = {a.id: a for a in activities}
    decisions: dict[str, Decision] = {}
    lines: list[Line] = []

    for w in sorted(work, key=lambda w: (w.work_date, w.unit_id)):
        unit = items.get(w.unit_id)
        if unit is None:            # the task no longer exists - there is nothing to show
            continue
        parent = items.get(unit.parent_id or "")
        dec = decisions.get(unit.id)
        if dec is None:
            dec = decisions[unit.id] = decide(_work_text(unit, parent), activities, mappings)
        summary, clipped = build_summary([(unit, parent)], max_summary_chars)
        line = Line(
            key=_line_key(w.work_date, [f"{unit.kind}:{unit.id}"]),
            work_date=w.work_date, minutes=max(0, w.minutes), status=dec.status, tier=dec.tier,
            activity_id=dec.activity_id, score=dec.score, reason=dec.reason,
            suggestions=dec.suggestions, summary=summary,
            sources=[Source(unit.kind, unit.id, max(0, w.minutes), parent.title if parent else unit.title,
                            unit.title if unit.kind == "subtask" else None)],
            units=[_work_text(unit, parent)],
            needs_hours=w.minutes <= 0,
        )
        _apply_activity_fields(line, acts_by_id, summary)
        if clipped:
            line.flags.append(SUMMARY_CLIPPED)
        lines.append(line)

    _flag_overfull_days(lines)
    for line in lines:
        line.selected = line.status == MATCHED and not _blocking(line) and not line.needs_hours
    lines.sort(key=lambda l: (l.work_date, _STATUS_ORDER[l.status], l.key))
    return lines


_STATUS_ORDER = {MATCHED: 0, NEEDS_SUB: 1, AMBIGUOUS: 2, UNMATCHED: 3}
_DUPLICATE_FLAGS = {ALREADY_IN_HRMS, ALREADY_SENT, DAY_ALREADY_SENT}
_BLOCKING_FLAGS = _DUPLICATE_FLAGS | {DAY_OVER_24H}


def _blocking(line: Line) -> bool:
    return any(f in _BLOCKING_FLAGS for f in line.flags)


def _flag_overfull_days(lines: Sequence[Line]) -> None:
    per_day: dict[date, int] = defaultdict(int)
    for l in lines:
        per_day[l.work_date] += l.minutes
    for l in lines:
        if per_day[l.work_date] > MINUTES_PER_DAY and DAY_OVER_24H not in l.flags:
            l.flags.append(DAY_OVER_24H)


# ---------------------------------------------------------------- duplicates


def apply_existing(
    lines: Sequence[Line],
    hrms_entries: Sequence[ExistingEntry],
    sent_here: Sequence[ExistingEntry] = (),
) -> None:
    """Flag lines that would duplicate what is already on the HRMS timesheet
    (`hrms_entries`, only if the HRMS can be read) or was already sent from
    CloseLoop (`sent_here`), and deselect them.

    Same date + activity is a duplicate. And because the HRMS takes a whole DAY in
    one request, ANY line on a date CloseLoop already sent is held back
    (DAY_ALREADY_SENT): sending that date again could replace what is there, and
    that is not known. Other HRMS entries that day only raise a notice (and can
    push the day past 24h)."""

    def index(rows: Sequence[ExistingEntry]):
        out: dict[tuple, float] = defaultdict(float)
        for r in rows:
            out[(r.work_date, r.activity_id)] += r.hours
        return out

    on_hrms, mine = index(hrms_entries), index(sent_here)
    sent_days = {r.work_date for r in sent_here}
    day_hours: dict[date, float] = defaultdict(float)
    for r in list(hrms_entries) + list(sent_here):
        day_hours[r.work_date] += r.hours

    for line in lines:
        key = (line.work_date, line.activity_id)
        if line.activity_id is not None and key in mine:
            line.flags.append(ALREADY_SENT)
            line.existing_hours = round(mine[key], 2)
        elif line.work_date in sent_days:
            line.flags.append(DAY_ALREADY_SENT)
        if line.activity_id is not None and key in on_hrms:
            line.flags.append(ALREADY_IN_HRMS)
            line.existing_hours = round(on_hrms[key], 2)
        if day_hours.get(line.work_date) and not any(f in _DUPLICATE_FLAGS for f in line.flags):
            line.flags.append(OTHER_HRMS_ENTRIES)
        if day_hours.get(line.work_date, 0) * 60 + _day_minutes(lines, line.work_date) > MINUTES_PER_DAY \
                and DAY_OVER_24H not in line.flags:
            line.flags.append(DAY_OVER_24H)
        if _blocking(line):
            line.selected = False


def _day_minutes(lines: Sequence[Line], day: date) -> int:
    return sum(l.minutes for l in lines if l.work_date == day)


# ---------------------------------------------------------------- totals


def totals(lines: Sequence[Line], filed_tasks: int = 0, filed_minutes: int = 0) -> dict:
    """Where the hours of the shown rows stand, and what was filed earlier."""
    ready = sum(l.minutes for l in lines if l.status == MATCHED and not _blocking(l))
    duplicate = sum(
        l.minutes for l in lines
        if l.status == MATCHED and any(f in _DUPLICATE_FLAGS for f in l.flags)
    )
    review = sum(l.minutes for l in lines if l.status != MATCHED)
    held = sum(
        l.minutes for l in lines
        if l.status == MATCHED and _blocking(l) and not any(f in _DUPLICATE_FLAGS for f in l.flags)
    )
    return {
        "tasks": len(lines),
        "reported_minutes": sum(l.minutes for l in lines),
        "hours_missing": sum(1 for l in lines if l.needs_hours),
        "ready_minutes": ready,
        "already_in_hrms_minutes": duplicate,
        "needs_review_minutes": review,
        "held_minutes": held,
        "selected_minutes": sum(l.minutes for l in lines if l.selected),
        "already_filed_tasks": filed_tasks,
        "already_filed_minutes": filed_minutes,
    }


# ---------------------------------------------------------------- submit-side


@dataclass
class Chosen:
    """A line as the user finally wants it sent."""

    work_date: date
    activity_id: int
    minutes: int
    summary: str
    work_type: str
    sources: list[Source]
    sub_values: dict[int, str] = field(default_factory=dict)
    tier: Optional[str] = None
    score: float = 0.0
    reason: str = ""
    units: list[WorkText] = field(default_factory=list)

    @property
    def hours(self) -> float:
        return hours_from_minutes(self.minutes)


class MergeConflict(Exception):
    """Two lines the user put on the same date and activity disagree about
    something that can only have one value."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def find_duplicates(
    chosen: Sequence[Chosen],
    hrms_entries: Sequence[ExistingEntry],
    sent_here: Sequence[ExistingEntry] = (),
) -> list[Chosen]:
    """Final entries that would repeat something already on the HRMS, or that fall
    on a date CloseLoop already sent (a day goes out once). Checked on the MERGED
    entries, i.e. after the user's own activity changes, which the preview could
    not know about."""
    taken = {(e.work_date, e.activity_id) for e in list(hrms_entries) + list(sent_here)}
    sent_days = {e.work_date for e in sent_here}
    return [c for c in chosen if (c.work_date, c.activity_id) in taken or c.work_date in sent_days]


def merge_chosen(chosen: Sequence[Chosen], max_chars: int) -> list[Chosen]:
    """One HRMS entry per (date, activity): lines the user ended up putting on the
    same activity are combined (minutes summed, then rounded once; summaries
    joined, repeats dropped). Their work type must match, and so must any
    sub-activity values they both filled in - otherwise MergeConflict, because the
    HRMS entry can only carry one."""
    merged: dict[tuple, Chosen] = {}
    parts: dict[tuple, list[str]] = defaultdict(list)
    for c in chosen:
        key = (c.work_date, c.activity_id)
        if key not in merged:
            merged[key] = Chosen(
                c.work_date, c.activity_id, 0, "", c.work_type, [], dict(c.sub_values),
                c.tier, c.score, c.reason,
            )
        m = merged[key]
        if c.work_type != m.work_type:
            raise MergeConflict(
                "WORK_TYPE_CONFLICT",
                f"{c.work_date.isoformat()}: lines on the same activity have different work types. "
                "Give them the same one.",
            )
        for sid, val in c.sub_values.items():
            have = (m.sub_values.get(sid) or "").strip()
            new = (val or "").strip()
            if have and new and have != new:
                raise MergeConflict(
                    "VALUES_CONFLICT",
                    f"{c.work_date.isoformat()}: lines on the same activity have different values for the "
                    "same extra field. Make them match.",
                )
            if not have:
                m.sub_values[sid] = new
        m.minutes += c.minutes
        m.sources.extend(c.sources)
        m.units.extend(u for u in c.units if u not in m.units)
        parts[key].extend(p for p in c.summary.split(" | ") if p)
    for key, m in merged.items():
        seen: set[str] = set()
        m.summary = clip(" | ".join(p for p in parts[key] if not (p in seen or seen.add(p))), max_chars)[0]
    return sorted(merged.values(), key=lambda c: (c.work_date, c.activity_id))
