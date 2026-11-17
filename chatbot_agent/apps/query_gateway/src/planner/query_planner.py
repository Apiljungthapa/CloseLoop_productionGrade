from domain.models import QueryPlan
from shared.constants.enums import Route
from shared.schemas.user_query import UserQuery

from .intent_router import classify


class QueryPlanner:
    def __init__(self, db_enabled: bool) -> None:
        self.db_enabled = db_enabled

    def plan(self, query: UserQuery) -> QueryPlan:
        route, notes = classify(query.text)
        # Never route to a capability that is switched off: fall back to documents
        if route in (Route.DB, Route.HYBRID) and not self.db_enabled:
            notes.append(f"db disabled: {route.value} -> doc")
            route = Route.DOC
        return QueryPlan(route=route, top_k=query.top_k, document_id=query.document_id, notes=notes)
