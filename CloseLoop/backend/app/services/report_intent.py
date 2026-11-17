"""Turn a sentence like "Prepare a report of pending tasks by team member as Excel" into a report request.

Deterministic on purpose: no model call, so it is instant, free, identical every time and unit-testable.
Two stages:

    1. ``looks_like_report_request(text)``  - is this a request to PRODUCE a report (as opposed to a question
                                              about reports, such as "how do I generate a report")?
    2. ``interpret(text, context)``         - pick the report kind, output format(s), period(s), entity and
                                              person, resolve them against the caller's own data, and either
                                              return a ready :class:`ReportSpec` or ONE clarifying question.

The context holds only what the caller may see (their entities, the tenant's periods and users), so a name
that is not in it simply does not resolve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from ..calendar import BS_MONTHS, bs_month_length, to_ad, to_bs
from .report_studio import (
    KIND_CLOSE_STATUS, KIND_COMPARISON, KIND_COMPLETION, KIND_MEMBERS, KIND_OVERDUE, LABELS, PeriodInfo, ReportSpec,
)

FORMATS = ("dashboard", "pdf", "xlsx", "csv")
DOWNLOAD_FORMATS = ("pdf", "xlsx", "csv")


# --------------------------------------------------------------------------------------------
# Stage 1: is it a request to produce a report?
# --------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


_VERB = (
    r"(?:generate|generating|prepare|preparing|create|creating|make|build|produce|run|compile|draw up|put together|"
    r"give me|get me|send me|show me|pull|export|download|need|want|would like|i'd like|can i get|can you get)"
)
_NOUN = (
    r"(?:reports?|summary|summaries|comparison|analysis|breakdown|overview|scorecard|status update|"
    r"spreadsheet|workbook|pdf|excel|xlsx|csv)"
)
_QUESTION_ABOUT_REPORTS = re.compile(
    r"^(?:what|how|where|why|when|who|which|is there|are there|explain|tell me (?:how|about|what)|can (?:i|we) (?:see|find)|"
    r"do (?:i|we) have|does)\b", re.IGNORECASE)
_REPORT_AS_VERB = re.compile(r"\breport\s+(?:a|an|the|my|this|that|it)\b|\b(?:to|and|please)\s+report\b(?!\s+(?:of|on|for|about))", re.IGNORECASE)
_COMPARE = re.compile(
    r"\bcompar(?:e|ing|ison)\b.*\b(?:period|month|quarter|cycle|last|previous|prior|p\d+|versus|vs|with|to|and)\b|"
    r"\b(?:this|current)\s+(?:period|month)\s+(?:vs\.?|versus|against|compared (?:to|with))\b", re.IGNORECASE)


def looks_like_report_request(text: str) -> bool:
    """True for "generate the monthly task completion report"; False for "how do I generate a report"."""
    t = _norm(text)
    if len(t) < 8 or len(t) > 600:
        return False
    if _QUESTION_ABOUT_REPORTS.match(t) and not re.match(r"^(?:what|how) (?:is|are|was|were) (?:the )?(?:status|progress)\b", t):
        # "what is a report" / "how do I generate a report" are questions for the documentation.
        if not re.search(r"\b(?:compare|comparison)\b", t):
            return False
    if _REPORT_AS_VERB.search(t) and not re.search(r"\breports?\s+(?:of|on|for|about|by|as)\b", t):
        return False
    if _COMPARE.search(t) and re.search(r"\b(?:period|month|cycle|quarter|p\d+)\b", t):
        return True
    verb_noun = re.search(rf"\b{_VERB}\b.*\b{_NOUN}\b", t) or re.search(rf"\b{_NOUN}\b.*\b(?:please|for me)\b", t)
    if verb_noun:
        return True
    # "monthly task completion report", "overdue report in pdf", "report on pending tasks"
    if re.search(r"\b(?:[a-z-]+\s+){0,5}reports?\b", t) and re.search(
            r"\b(?:task|tasks|period|overdue|pending|completion|close|closing|entity|entities|team|member|progress|workload|kpi|monthly|weekly|status)\b", t):
        return not _QUESTION_ABOUT_REPORTS.match(t)
    return False


# --------------------------------------------------------------------------------------------
# Stage 2: interpret
# --------------------------------------------------------------------------------------------
_KIND_RULES: list[tuple[str, re.Pattern]] = [
    (KIND_COMPARISON, re.compile(
        r"\bcompar(?:e|ing|ison|ed)\b|\bversus\b|\bvs\.?\b|\bagainst (?:the )?(?:last|previous|prior)\b|\btrends?\b|"
        r"\bperiod[- ]over[- ]period\b|\bmonth[- ]over[- ]month\b|\bdifference between\b|\bchange since\b|\bimproved\b")),
    (KIND_CLOSE_STATUS, re.compile(
        r"\bclose[- ]?status\b|\bclosing status\b|\bclose[- ]?out status\b|\bclose readiness\b|\bready to close\b|"
        r"\bentity (?:status|readiness)\b|\bstatus (?:of|for) (?:all |the |every |each )?(?:entit(?:y|ies)|plans?|packs?)\b|"
        r"\b(?:all|every|each) entit(?:y|ies)\b.*\b(?:status|close|closing|readiness)\b|\bhow (?:close|ready) (?:are|is)\b")),
    (KIND_MEMBERS, re.compile(
        r"\bby (?:team )?(?:members?|person|people|users?|employees?|owners?|assignees?|staff)\b|"
        r"\bper (?:team )?(?:member|person|user|employee|owner|assignee)\b|\beach (?:team )?(?:member|person|user|employee)\b|"
        r"\bworkloads?\b|\bwho (?:has|is holding|owns)\b|\bteam members?\b|\bpeople\b")),
    (KIND_OVERDUE, re.compile(
        r"\boverdue\b|\blate\b|\bdelayed\b|\bpast due\b|\bbehind schedule\b|\bageing\b|\baging\b|\bmissed deadlines?\b")),
    (KIND_COMPLETION, re.compile(
        r"\bcompletion\b|\bcompleted\b|\bprogress\b|\bperformance\b|\bmonthly\b|\bperiod report\b|\btask (?:summary|report)\b|"
        r"\bsummary\b|\bkpis?\b|\boverview\b")),
]
_FORMAT_RULES: list[tuple[str, re.Pattern]] = [
    ("pdf", re.compile(r"\bpdf\b")),
    ("xlsx", re.compile(r"\bexcel\b|\bxlsx?\b|\bspreadsheet\b|\bworkbook\b")),
    ("csv", re.compile(r"\bcsv\b|\bcomma[- ]separated\b")),
    ("dashboard", re.compile(r"\bdashboard\b|\bcharts?\b|\bgraphs?\b|\bvisual(?:s|ly|ise|ize)?\b|\bon[- ]screen\b")),
]

_AD_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6,
    "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_BS_ALIASES = {
    "baisakh": 1, "baishakh": 1, "baisak": 1, "jestha": 2, "jeth": 2, "ashadh": 3, "asar": 3, "ashar": 3, "shrawan": 4,
    "srawan": 4, "saun": 4, "bhadra": 5, "bhadau": 5, "ashwin": 6, "asoj": 6, "ashoj": 6, "kartik": 7, "kattik": 7,
    "mangsir": 8, "mangshir": 8, "poush": 9, "paush": 9, "push": 9, "magh": 10, "falgun": 11, "fagun": 11,
    "chaitra": 12, "chait": 12,
}
assert set(_BS_ALIASES.values()) == set(BS_MONTHS)  # every Nepali month has at least one spelling
_PREP = r"(?:for|in|of|during|with|and|vs\.?|versus|to|against|than|from|between|on|compare|comparing|month of)"


@dataclass
class Ref:
    kind: str  # current | previous | label | month_ad | month_bs
    value: object = None
    pos: int = 0
    text: str = ""


def _find_refs(t: str, periods: list[PeriodInfo]) -> list[Ref]:
    refs: list[Ref] = []
    for m in re.finditer(r"\b(?:this|current|running|present|ongoing)\s+(?:reporting\s+)?(?:period|month|cycle)\b", t):
        refs.append(Ref("current", None, m.start(), m.group(0)))
    for m in re.finditer(
            r"\b(?:last|previous|prior|preceding)\s+(?:reporting\s+)?(?:period|month|cycle)\b|\bthe previous\b|\bprevious\b", t):
        if not any(r.kind == "previous" and abs(r.pos - m.start()) < 20 for r in refs):
            refs.append(Ref("previous", None, m.start(), m.group(0)))
    claimed: list[tuple[int, int]] = []
    for p in periods:  # exact period labels ("P11", "Q3", "P001")
        for key in {p.label.lower().strip(), p.label.lower().split()[0] if p.label.split() else ""}:
            if not key:
                continue
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", t):
                refs.append(Ref("label", p.id, m.start(), m.group(0)))
                claimed.append((m.start(), m.end()))
    for m in re.finditer(r"(?<![a-z0-9])p\s*-?\s*(\d{1,3})(?![a-z0-9])|\bperiod\s+(\d{1,3})\b", t):
        n = int(m.group(1) or m.group(2))
        if any(a <= m.start() < b for a, b in claimed):
            continue
        for p in periods:
            tail = re.match(r"^[a-z]+\s*-?\s*0*(\d+)", p.label.lower())
            head = re.match(r"^p", p.label.lower())
            if tail and head and int(tail.group(1)) == n:
                refs.append(Ref("label", p.id, m.start(), m.group(0)))
                break
    for name, number in _BS_ALIASES.items():
        for m in re.finditer(rf"\b{name}\b", t):
            refs.append(Ref("month_bs", number, m.start(), m.group(0)))
    for name, number in _AD_MONTHS.items():
        for m in re.finditer(rf"\b{_PREP}\s+{name}\b", t):
            refs.append(Ref("month_ad", number, m.start() + m.group(0).rfind(name), name))
    # one reference per spot, in reading order
    refs.sort(key=lambda r: r.pos)
    out: list[Ref] = []
    for r in refs:
        if out and abs(out[-1].pos - r.pos) < 3 and out[-1].kind == r.kind:
            continue
        out.append(r)
    return out


def _overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
    return max(0, (min(a_end, b_end) - max(a_start, b_start)).days + 1)


def _current(periods: list[PeriodInfo], today: date) -> Optional[PeriodInfo]:
    covering = [p for p in periods if p.start_date <= today <= p.end_date]
    if covering:
        return max(covering, key=lambda p: p.start_date)
    past = [p for p in periods if p.end_date < today]
    if past:
        return max(past, key=lambda p: p.end_date)
    return min(periods, key=lambda p: p.start_date) if periods else None


def _previous_of(periods: list[PeriodInfo], p: PeriodInfo) -> Optional[PeriodInfo]:
    earlier = [q for q in periods if q.end_date < p.start_date]
    return max(earlier, key=lambda q: q.end_date) if earlier else None


def _month_range(kind: str, number: int, year: int) -> Optional[tuple[date, date]]:
    try:
        if kind == "month_ad":
            first = date(year, number, 1)
            nxt = date(year + (number == 12), number % 12 + 1, 1)
            return first, nxt - timedelta(days=1)
        return to_ad(year, number, 1), to_ad(year, number, bs_month_length(year, number))
    except (ValueError, KeyError, OverflowError):
        return None


def _by_month(periods: list[PeriodInfo], ref: Ref, today: date) -> Optional[PeriodInfo]:
    """The period that best covers a named month, preferring the most recent such month not in the future."""
    years = [today.year, today.year - 1, today.year + 1] if ref.kind == "month_ad" else \
        [to_bs(today).year, to_bs(today).year - 1, to_bs(today).year + 1]
    best: Optional[tuple[tuple, PeriodInfo]] = None
    for y in years:
        rng = _month_range(ref.kind, int(ref.value), y)
        if rng is None:
            continue
        future = rng[0] > today
        for p in periods:
            ov = _overlap(p.start_date, p.end_date, rng[0], rng[1])
            if ov <= 0:
                continue
            score = (ov - (1000 if future else 0), p.start_date <= today <= p.end_date, p.start_date)
            if best is None or score > best[0]:
                best = (score, p)
    return best[1] if best else None


@dataclass
class Context:
    periods: list[PeriodInfo]
    entities: dict[str, str]
    users: dict[str, str]
    today: date


@dataclass
class Interpretation:
    understood: bool
    spec: Optional[ReportSpec] = None
    fmt: str = "dashboard"
    formats: list[str] = field(default_factory=list)
    message: str = ""
    assumptions: list[str] = field(default_factory=list)
    clarification: Optional[str] = None

    def to_dict(self) -> dict:
        s = self.spec
        return {
            "understood": self.understood, "format": self.fmt, "formats": self.formats, "message": self.message,
            "assumptions": self.assumptions, "clarification": self.clarification,
            "spec": None if s is None else {
                "kind": s.kind, "period_id": s.period_id, "compare_period_id": s.compare_period_id,
                "entity_id": s.entity_id, "member_id": s.member_id},
        }


def detect_kind(t: str) -> str:
    for kind, rule in _KIND_RULES:
        if rule.search(t):
            return kind
    return KIND_COMPLETION


def detect_formats(t: str) -> list[str]:
    found = [(m.start(), name) for name, rule in _FORMAT_RULES if (m := rule.search(t))]
    return [name for _pos, name in sorted(found)]


def _match_entity(t_raw: str, entities: dict[str, str]) -> list[str]:
    hits = []
    for eid, name in entities.items():
        n = name.strip()
        if len(n) < 2:
            continue
        flags = 0 if len(n) <= 3 else re.IGNORECASE  # "DE" must be typed as DE, not the word "de"
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", t_raw, flags):
            hits.append((len(n), eid))
    return [eid for _l, eid in sorted(hits, reverse=True)]


def _match_people(t: str, users: dict[str, str]) -> tuple[list[str], list[str]]:
    """(exact full-name matches, first-name-only matches)."""
    full, first = [], []
    for uid, name in users.items():
        lname = name.strip().lower()
        if len(lname) < 3:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(lname)}(?![a-z0-9])", t):
            full.append(uid)
            continue
        token = lname.split()[0]
        if len(token) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(token)}(?:'s)?(?![a-z0-9])", t):
            first.append(uid)
    return full, first


_STOP_FIRST_NAMES = {"the", "all", "team", "report", "period", "tasks", "pending", "open", "close", "admin", "for", "and"}


def _fmt_range(p: PeriodInfo) -> str:
    return f"{p.start_date:%d %b} - {p.end_date:%d %b %Y}"


def interpret(text: str, ctx: Context) -> Interpretation:
    t = _norm(text)
    if not ctx.periods:
        return Interpretation(False, clarification="No periods are set up yet, so there is nothing to report on.")

    kind = detect_kind(t)
    formats = detect_formats(t)
    refs = _find_refs(t, ctx.periods)
    assumptions: list[str] = []

    # people & entities
    full, first = _match_people(t, ctx.users)
    first = [u for u in first if ctx.users[u].split()[0].lower() not in _STOP_FIRST_NAMES]
    person: Optional[str] = None
    if len(full) == 1:
        person = full[0]
    elif len(full) > 1:
        full = sorted(full, key=lambda u: -len(ctx.users[u]))
        person = full[0]
    elif len(first) == 1:
        person = first[0]
    elif len(first) > 1:
        names = ", ".join(ctx.users[u] for u in first[:5])
        return Interpretation(False, formats=formats, clarification=f"I found more than one person that could mean: {names}. Which one do you want?")
    entity_hits = _match_entity(text, ctx.entities)
    entity = entity_hits[0] if entity_hits else None
    if person and kind in (KIND_COMPLETION, KIND_OVERDUE):
        kind = KIND_MEMBERS
        assumptions.append(f"Showing {ctx.users[person]}'s work in the pending-by-member report.")

    # periods
    current = _current(ctx.periods, ctx.today)
    resolved: list[PeriodInfo] = []
    for r in refs:
        found: Optional[PeriodInfo] = None
        if r.kind == "current":
            found = current
        elif r.kind == "previous":
            found = None  # relative: settled below
        elif r.kind == "label":
            found = next((p for p in ctx.periods if p.id == r.value), None)
        else:
            found = _by_month(ctx.periods, r, ctx.today)
            if found is None:
                recent = sorted(ctx.periods, key=lambda p: p.start_date)[-6:]
                known = "; ".join(f"{p.label} ({_fmt_range(p)})" for p in recent)
                return Interpretation(False, formats=formats, clarification=(
                    f"I couldn't find a period for \"{r.text}\". Recent periods: {known}. Which one do you want?"))
        if found is not None and found.id not in [p.id for p in resolved]:
            resolved.append(found)
    wants_previous = any(r.kind == "previous" for r in refs)
    named_spots = {r.pos for r in refs}
    for m in re.finditer(r"(?<![a-z0-9])p\s*-?\s*(\d{1,3})(?![a-z0-9])|\bperiod\s+(\d{1,3})\b", t):
        if m.start() not in named_spots and not any(abs(m.start() - pos) < 4 for pos in named_spots):
            recent = sorted(ctx.periods, key=lambda p: p.start_date)[-6:]
            known = "; ".join(f"{p.label} ({_fmt_range(p)})" for p in recent)
            shown = re.sub(r"\s+|-", "", m.group(0)).upper() if re.fullmatch(r"p\s*-?\s*\d+", m.group(0)) else m.group(0)
            return Interpretation(False, formats=formats, clarification=(
                f"I couldn't find a period called \"{shown}\". Recent periods: {known}. Which one do you want?"))

    main: Optional[PeriodInfo]
    compare: Optional[PeriodInfo] = None
    if kind == KIND_COMPARISON:
        if len(resolved) >= 2:
            a, b = sorted(resolved[:2], key=lambda p: p.start_date)
            main, compare = b, a
        elif len(resolved) == 1:
            main = resolved[0]
            compare = _previous_of(ctx.periods, main)
            if wants_previous and main.id == (current.id if current else None):
                assumptions.append(f"Compared with the period before {main.label}.")
        else:
            main = current
            compare = _previous_of(ctx.periods, main) if main else None
            assumptions.append("No periods named, so I compared the current period with the one before it.")
        if compare is None:
            return Interpretation(False, formats=formats, clarification=(
                f"There is no period before {main.label if main else 'the current one'} to compare it with. "
                f"Try a report for a single period instead."))
    else:
        if resolved:
            main = resolved[0]
        elif wants_previous and current:
            main = _previous_of(ctx.periods, current) or current
            if main.id == current.id:
                assumptions.append("There is no earlier period, so I used the current one.")
        else:
            main = current
            if main:
                assumptions.append(f"No period named, so I used the current one ({main.label}).")

    if re.search(r"\b(?:quarter|quarterly|annual|yearly|year[- ]to[- ]date|ytd|fiscal year)\b", t):
        assumptions.append("Quarter and year views are not available yet; this report covers one period.")

    fmt = formats[0] if formats else "dashboard"
    spec = ReportSpec(
        kind=kind, period_id=main.id if main else None, compare_period_id=compare.id if compare else None,
        entity_id=entity, member_id=person if kind == KIND_MEMBERS else None,
    )
    parts = [f"the {LABELS[kind]}"]
    if kind == KIND_COMPARISON and main and compare:
        parts.append(f"for {main.label} against {compare.label}")
    elif main:
        parts.append(f"for {main.label}")
    parts.append(f"({ctx.entities[entity]})" if entity else "(all entities)")
    if person and kind == KIND_MEMBERS:
        parts.append(f"for {ctx.users[person]}")
    shown = {"dashboard": "as a dashboard", "pdf": "as a PDF", "xlsx": "as an Excel workbook", "csv": "as a CSV file"}[fmt]
    message = f"I'll prepare {' '.join(parts)} {shown}."
    return Interpretation(True, spec=spec, fmt=fmt, formats=formats or ["dashboard"], message=message, assumptions=assumptions)
