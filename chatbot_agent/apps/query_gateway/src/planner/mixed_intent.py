"""Spot a message that asks a documentation question AND a records question at once.

"what is close packs and who is devraj khatri" needs the documents for one half and the database for
the other. The language model that routes messages usually notices, but it is a judgement call, and a
wrong one silently drops half of the answer. This deterministic check backs it up (and covers the
keyword fallback used when the model is unavailable). It can only UPGRADE a doc-only or db-only
decision to "hybrid"; it never downgrades one, and it stays quiet for a message with a single part.

The message is cut into parts at "?", ";", line breaks, commas, "&" and the words and / also / plus /
aur / ra, and each part is labelled:

    doc      asks for an explanation        "what is a close pack", "how do I ...", "define overdue"
    db       asks about live records        "who is devraj khatri", "how many tasks ...", "list ..."
    concept  just names a product term      "close pack"
    name     looks like a person's name     "devraj khatri"
    other    anything else

A message is mixed when a doc part sits next to a db or name part, or a bare concept next to a name
("close pack, devraj khatri"). A bare concept next to an ordinary db part is NOT mixed: "list all tasks
and periods" wants two lists, not an explanation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PARTS = 6

_SPLIT = re.compile(r"[?;\n]+|,|\s*&\s*|\s+(?:and|also|plus|then|aur|ra|tatha)\s+", re.IGNORECASE)
_EDGE = " \t.!:-\"'`*()[]"

# "how do I / show me how / steps to" is a documentation question even if it mentions a record word.
_HOWTO = re.compile(
    r"\bhow\s+(?:do|can|should|to|does|would)\b|\bshow\s+me\s+how\b|\bsteps?\s+(?:to|for)\b", re.IGNORECASE
)

# Wording that can only be about the tenant's own live data.
_STRONG_DB = re.compile(
    "|".join(
        [
            r"\bwho(?:'s|\s+(?:is|are|was|were|has|have|did|does|owns?))\b",
            r"\bwhose\b",
            r"\bhow\s+(?:many|much)\b",
            r"\b(?:count|number|total|sum|average)\s+of\b",
            r"\btotal\s+number\b",
            r"\b(?:list|show)\b",
            r"\bwhich\s+(?:tasks?|users?|people|persons?|members?|leads?|periods?|plans?|entit(?:y|ies)|"
            r"packs?|close\s*-?\s*packs?)\b",
            r"\bdo\s+we\s+have\b|\bhave\s+we\b|\bare\s+there\b",
            r"\bcurrent\s+(?:period|plan|status|week|month)\b|\bopen\s+period\b",
            r"\b(?:today|tomorrow|yesterday)\b",
            r"\b(?:this|last|next)\s+(?:week|month|quarter|year|period)\b",
            r"\blast\s+log\s*in\b|\blogged\s+in\b",
            r"\bemail\s+of\b|\bassigned\s+to\b|\bstatus\s+of\b",
            r"\bko\s+ho\b|\bkaun\b|\bkitn[ea]\b|\bkati\b",  # Nepali / Hindi: who is / who / how many
        ]
    ),
    re.IGNORECASE,
)

_DOC = re.compile(
    "|".join(
        [
            r"\bwhat(?:\s+(?:is|are|was|does|do)|'s|s)\b",
            r"\b(?:explain|describe|define|definition|meaning|means|overview|clarify|elaborate|"
            r"summari[sz]e|guide|policy|procedure|process|why)\b",
            r"\bdifference\s+between\b|\btell\s+me\s+about\b",
            r"\bkya\s+(?:hai|hota|hoti|hain)\b|\b(?:k|ke)\s+ho\b|\bkasto\b",
        ]
    ),
    re.IGNORECASE,
)

# Status words: they point at records, but "define overdue" is still a documentation question, so they
# only count when no explanation wording is present.
_WEAK_DB = re.compile(
    r"\b(?:overdue|pending|completed|in\s+progress|not\s+started|rejected|late|due)\b", re.IGNORECASE
)

# A clause that points back at the previous one ("...and what are THEIR names", "...and what is HIS role") or
# asks for an attribute of a record (names, e-mails, roles, a rate...) continues the records question; it is not
# a separate documentation question. The net only ever ADDS the documents, so when in doubt it stays quiet and
# the router's own judgement stands.
_FOLLOW_ON = re.compile(
    r"\b(?:their|them|they|his|her|its|it|he|she|those|these|each|per)\b|"
    r"\b(?:names?|e-?mails?|roles?|titles?|status(?:es)?|priorit(?:y|ies)|owners?|assignees?|log\s*in|dates?|ids?|"
    r"rate|percentage|percent|average|count|total|oldest|newest|latest)\b",
    re.IGNORECASE,
)

_CONCEPT_WORDS = (
    r"close\s*-?\s*packs?|closepacks?|periods?|plans?|entit(?:y|ies)|sub\s*-?\s*tasks?|tasks?|"
    r"team\s+leads?|team\s+members?|super\s+admins?|carry\s*-?\s*forwards?|templates?|"
    r"recurring\s+tasks?|due\s+dates?|reviewers?|assignees?|priorit(?:y|ies)|notifications?|comments?|"
    r"attachments?|time\s+entr(?:y|ies)|reports?|versions?|roles?"
)
_CONCEPT = re.compile(rf"\b(?:{_CONCEPT_WORDS})\b", re.IGNORECASE)
# Distinctive product terms only: a lone "tasks" next to a name ("devraj khatri, tasks") means "his
# tasks", not "explain tasks", so generic words never make a bare-concept part.
_BARE_CONCEPT = re.compile(
    r"\b(?:close\s*-?\s*packs?|closepacks?|periods?|entit(?:y|ies)|sub\s*-?\s*tasks?|team\s+leads?|"
    r"super\s+admins?|carry\s*-?\s*forwards?|templates?|recurring\s+tasks?)\b",
    re.IGNORECASE,
)

_STOP = {
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "is", "are", "and", "or", "me", "my", "our",
    "please", "pls", "plz", "about", "with", "by", "from", "it", "this", "that", "these", "those",
}
_COMMON = {
    "all", "info", "information", "details", "detail", "summary", "data", "help", "hello", "hi", "hey",
    "thanks", "thank", "you", "ok", "okay", "yes", "no", "good", "morning", "evening", "bye",
}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z.'-]*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class MixedIntent:
    """The split of a mixed message: what to ask the documents and what to ask the database."""

    doc_query: str
    db_query: str


def split_parts(text: str) -> list[str]:
    parts = []
    for piece in _SPLIT.split(text or ""):
        piece = (piece or "").strip(_EDGE)
        if piece:
            parts.append(piece)
    return parts[:MAX_PARTS]


def _is_concept_only(part: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9-]+", part.lower()) if w not in _STOP]
    if not words:
        return False
    joined = " ".join(words)
    return bool(_BARE_CONCEPT.search(joined)) and not _BARE_CONCEPT.sub(" ", joined).strip()


def _looks_like_name(part: str) -> bool:
    """Two to four plain words (or an e-mail address) that are not a question, a product term or filler."""
    if "@" in part and _TOKEN.fullmatch(part.strip()):
        return True
    tokens = re.findall(r"[A-Za-z][A-Za-z.'-]*", part)
    if not 2 <= len(tokens) <= 4 or re.search(r"[^A-Za-z.'\s-]", part):
        return False
    lowered = [t.lower() for t in tokens]
    if any(t in _STOP or t in _COMMON for t in lowered):
        return False
    return not _CONCEPT.search(part)


def classify_part(part: str) -> str:
    """'doc' | 'db' | 'concept' | 'name' | 'other' for one piece of a message."""
    if _HOWTO.search(part):
        return "doc"
    if _STRONG_DB.search(part):
        return "db"
    if _DOC.search(part):
        return "db" if _FOLLOW_ON.search(part) and not _BARE_CONCEPT.search(part) else "doc"
    if _WEAK_DB.search(part):
        return "db"
    if _is_concept_only(part):
        return "concept"
    if _looks_like_name(part):
        return "name"
    return "other"


def detect_mixed_intent(text: str) -> MixedIntent | None:
    """The doc/db split when ``text`` asks for documentation and records together, else None."""
    parts = split_parts(text)
    if len(parts) < 2:
        return None
    kinds = [(part, classify_part(part)) for part in parts]
    doc = [p for p, k in kinds if k == "doc"]
    db = [p for p, k in kinds if k == "db"]
    concept = [p for p, k in kinds if k == "concept"]
    names = [p for p, k in kinds if k == "name"]

    if doc and (db or names):
        doc_parts, db_parts = doc, db + names
    elif concept and names:
        doc_parts, db_parts = [f"what is {c}" for c in concept], names
    else:
        return None
    return MixedIntent(doc_query=" and ".join(doc_parts), db_query=" and ".join(db_parts))
