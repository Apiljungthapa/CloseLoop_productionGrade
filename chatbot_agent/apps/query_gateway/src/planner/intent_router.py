from shared.constants.enums import Route

from . import planner_rules as rules
from .mixed_intent import detect_mixed_intent

MIXED_NOTE = "message mixes a documentation question with a records question"


def classify(text: str) -> tuple[Route, list[str]]:
    """Rule-based intent detection. Returns the route and the reasons for it."""
    q = " ".join(text.lower().split())
    if q.strip(" !.?") in rules.CHITCHAT:
        return Route.CHITCHAT, ["greeting / small talk"]

    db_hits = [h for h in rules.DB_HINTS if h in q]
    doc_hits = [h for h in rules.DOC_HINTS if h in q]
    if db_hits and doc_hits:
        return Route.HYBRID, [f"db hints: {db_hits}", f"doc hints: {doc_hits}"]
    route, notes = (
        (Route.DB, [f"db hints: {db_hits}"])
        if db_hits
        else (Route.DOC, [f"doc hints: {doc_hits}"] if doc_hits else ["default: documents"])
    )
    # "what is a close pack and devraj khatri": each half carries a cue of its own, so look at the parts.
    if detect_mixed_intent(text) is not None:
        return Route.HYBRID, [*notes, MIXED_NOTE]
    return route, notes
