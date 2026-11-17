"""Internal models that never cross the HTTP boundary."""
from dataclasses import dataclass, field

from shared.constants.enums import Route


@dataclass
class QueryPlan:
    route: Route
    top_k: int = 5
    document_id: str | None = None
    notes: list[str] = field(default_factory=list)  # why the planner chose this route
