"""Which HRMS activity does a CloseLoop task belong to?

Pure functions - no database, no network - so every rule can be unit tested.

A task (or sub task) is matched in this order, and the first hit wins:

  1. MAPPING_TEMPLATE  a choice somebody already confirmed for the same
                       task/sub-task template (exact id, strongest evidence)
  2. MAPPING_TITLE     ... for the same normalised title (custom tasks)
  3. MAPPING_PARENT    a sub task inherits its parent task's confirmed choice
  4. TEXT              wording: the task's title / description against the
                       activity's name / description

Only a confident match is auto-selected. TEXT needs a score of at least
AUTO_SCORE, a lead of at least AUTO_MARGIN over the runner-up, and a shared
word that actually says something (not "monthly", "report", a bare number...).
A single-word activity name is never auto-matched by wording alone. Anything
less sure is AMBIGUOUS (top suggestions, nothing selected) or UNMATCHED.

Only the ACTIVITY is matched. An activity's sub activities are not alternatives
to pick from: the HRMS wants a value for each of them (they are extra fields of
that activity), which timesheet_builder fills in or asks the user for.

Nothing is ever invented: a remembered mapping whose activity no longer exists
in the HRMS is ignored, and an activity from another project's list is never
offered because the caller only passes the selected project's activities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Mapping, Optional, Sequence

AUTO_SCORE = 0.85
AUTO_MARGIN = 0.15
SUGGEST_MIN = 0.5      # below this a task is UNMATCHED rather than AMBIGUOUS
LIST_MIN = 0.3         # lowest score still worth listing as a suggestion
MAX_SUGGESTIONS = 3

MATCHED = "MATCHED"
NEEDS_SUB = "NEEDS_SUB"      # activity found, but its extra fields still need values (set by the builder)
AMBIGUOUS = "AMBIGUOUS"
UNMATCHED = "UNMATCHED"

MAPPING_TEMPLATE = "MAPPING_TEMPLATE"
MAPPING_TITLE = "MAPPING_TITLE"
MAPPING_PARENT = "MAPPING_PARENT"
TEXT = "TEXT"

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or the this "
    "that to was were will with into per via".split()
)
# Words that appear in nearly every finance task name: sharing one of them says
# nothing about which activity is meant.
_GENERIC = frozenset(
    "monthly weekly daily quarterly annual yearly report reporting task work "
    "update prepare preparation submit submission complete completion check "
    "final new related activity support review process".split()
)

_TAG = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(text: Optional[str]) -> str:
    """Lower-case words separated by single spaces; markup and punctuation gone."""
    if not text:
        return ""
    return " ".join(_NON_WORD.sub(" ", _TAG.sub(" ", text).lower()).split())


def _stem(word: str) -> str:
    """Plural / -ing / -ed folded away so "reviews", "reviewing" and "reviewed"
    all read as "review". Applied to both sides of every comparison, so it only
    has to be consistent, not linguistically perfect."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 5:
        for suffix in ("ing", "ed"):
            if word.endswith(suffix):
                return word[: -len(suffix)]
    return word


def tokens(text: Optional[str]) -> set[str]:
    return {
        _stem(w) for w in normalize(text).split() if w not in _STOPWORDS
    }


def distinctive(words: Iterable[str]) -> set[str]:
    """The words that identify something: not generic, not a bare number."""
    return {w for w in words if w not in _GENERIC and not w.isdigit()}


def name_key(text: Optional[str]) -> str:
    """Stable key for "the same title" - used to remember a mapping."""
    return " ".join(sorted(tokens(text)))[:300]


# ---------------------------------------------------------------- data


@dataclass(frozen=True)
class SubActivity:
    """An extra field of an activity: the HRMS wants a value for each."""

    id: int
    name: str
    description: Optional[str] = None
    key: Optional[str] = None
    data_type: Optional[int] = None


@dataclass(frozen=True)
class Activity:
    id: int
    name: str
    description: Optional[str] = None
    subs: tuple[SubActivity, ...] = ()

    @classmethod
    def from_hrms(cls, row: Mapping) -> "Activity":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row.get("description"),
            subs=tuple(
                SubActivity(s["id"], s["name"], s.get("description"), s.get("key"), s.get("data_type"))
                for s in row.get("sub_activities") or []
            ),
        )


@dataclass(frozen=True)
class WorkText:
    """What we know about one task or sub task, for matching."""

    title: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    parent_title: Optional[str] = None
    parent_template_id: Optional[str] = None

    def title_tokens(self) -> set[str]:
        return tokens(self.title) | tokens(self.parent_title)

    def body_tokens(self) -> set[str]:
        return tokens(self.description)


@dataclass(frozen=True)
class Candidate:
    activity_id: int
    score: float
    reason: str


@dataclass(frozen=True)
class Decision:
    status: str
    tier: Optional[str] = None
    activity_id: Optional[int] = None
    score: float = 0.0
    reason: str = ""
    suggestions: tuple[Candidate, ...] = field(default_factory=tuple)


# A remembered choice: (key_kind, key_value) -> activity_id
Mappings = Mapping[tuple[str, str], int]


def mapping_keys(work: WorkText) -> list[tuple[str, str, str]]:
    """[(key_kind, key_value, tier)] to look up, strongest first."""
    keys: list[tuple[str, str, str]] = []
    if work.template_id:
        keys.append(("template", work.template_id, MAPPING_TEMPLATE))
    if work.title and name_key(work.title):
        keys.append(("title", name_key(work.title), MAPPING_TITLE))
    if work.parent_template_id:
        keys.append(("template", work.parent_template_id, MAPPING_PARENT))
    if work.parent_title and name_key(work.parent_title):
        keys.append(("title", name_key(work.parent_title), MAPPING_PARENT))
    return keys


# ---------------------------------------------------------------- scoring


def _score_text(
    work: WorkText, name: str, description: Optional[str]
) -> tuple[float, str]:
    """(score 0..1, why) of one activity / sub activity against the work."""
    name_norm = normalize(name)
    name_tok = tokens(name)
    if not name_tok:
        return 0.0, ""
    title_norm = normalize(work.title)
    if title_norm and title_norm == name_norm:
        return 1.0, "title equals the activity name"

    title_tok = work.title_tokens()
    body_tok = work.body_tokens()
    dist_name = distinctive(name_tok)
    shared_dist = distinctive(title_tok & name_tok)
    best, why = 0.0, ""

    def offer(score: float, reason: str) -> None:
        nonlocal best, why
        if score > best:
            best, why = score, reason

    if dist_name and dist_name <= title_tok:
        # Every TELLING word of the activity name is in the task's title (its
        # everyday words may be missing). Two telling words are strong evidence;
        # one alone ("Development") is too weak to select on its own.
        offer(0.9 if len(dist_name) >= 2 else 0.8, "activity name is in the task title")
    if dist_name and name_tok <= title_tok:
        # EVERY word of the name is there (extra words in the title - a date, a
        # client - don't matter: "Dashboard review - Oct 28" contains "Dashboard
        # review"). A name of two or more words with one telling word is strong
        # enough even when its other word is an everyday one ("review").
        offer(0.9 if len(name_tok) >= 2 else 0.8, "activity name is in the task title")
    if shared_dist:
        union = title_tok | name_tok
        offer(len(title_tok & name_tok) / len(union), "title and name share words")
    if dist_name and title_norm:
        ratio = SequenceMatcher(None, title_norm, name_norm).ratio()
        if ratio >= 0.9:
            offer(ratio, "title is almost the activity name")
    if dist_name and dist_name <= body_tok:
        offer(0.6, "activity name is in the description")
    if description:
        desc_dist = distinctive(tokens(description))
        evidence = desc_dist & distinctive(title_tok | body_tok)
        if len(desc_dist) >= 2 and evidence:
            offer(min(0.7, len(evidence) / len(desc_dist)), "wording matches the activity description")
    return round(best, 3), why


def rank_activities(work: WorkText, activities: Sequence[Activity]) -> list[Candidate]:
    """One candidate per activity, best first."""
    ranked: list[Candidate] = []
    for act in activities:
        score, why = _score_text(work, act.name, act.description)
        if score >= LIST_MIN:
            ranked.append(Candidate(act.id, score, why))
    return sorted(ranked, key=lambda c: (-c.score, c.activity_id))


def _is_confident(top: Candidate, runner_up: Optional[Candidate]) -> bool:
    if top.score < AUTO_SCORE:
        return False
    return runner_up is None or top.score - runner_up.score >= AUTO_MARGIN


# ---------------------------------------------------------------- decision


def _from_mapping(
    work: WorkText, by_id: Mapping[int, Activity], mappings: Mappings
) -> Optional[Decision]:
    for kind, value, tier in mapping_keys(work):
        act_id = mappings.get((kind, value))
        if act_id is None:
            continue
        act = by_id.get(act_id)
        if act is None:
            continue  # the activity is gone from the HRMS - don't trust it
        return Decision(
            MATCHED, tier, act.id, 0.95,
            {
                MAPPING_TEMPLATE: "confirmed earlier for this task template",
                MAPPING_TITLE: "confirmed earlier for a task with this title",
                MAPPING_PARENT: "same as its parent task (confirmed earlier)",
            }[tier],
        )
    return None


def decide(
    work: WorkText, activities: Sequence[Activity], mappings: Optional[Mappings] = None
) -> Decision:
    by_id = {a.id: a for a in activities}
    remembered = _from_mapping(work, by_id, mappings or {})
    if remembered:
        return remembered

    ranked = rank_activities(work, activities)
    suggestions = tuple(ranked[:MAX_SUGGESTIONS])
    if not ranked or ranked[0].score < SUGGEST_MIN:
        return Decision(UNMATCHED, None, None, ranked[0].score if ranked else 0.0,
                        "no HRMS activity looks like this task", suggestions)

    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    if not _is_confident(top, runner):
        close = [c for c in ranked if c.score >= SUGGEST_MIN]
        why = (
            f"possibly '{by_id[top.activity_id].name}' - not sure enough to choose it for you"
            if len(close) == 1
            else "more than one activity could fit - choose one"
        )
        return Decision(AMBIGUOUS, TEXT, None, top.score, why, suggestions)

    return Decision(MATCHED, TEXT, top.activity_id, top.score, top.reason, suggestions)
