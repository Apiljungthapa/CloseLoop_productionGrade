from shared.schemas.retrieval_result import RetrievedItem


def merge(evidence: list[RetrievedItem], limit: int = 10) -> list[RetrievedItem]:
    """Combine evidence from all routes, best first. Database rows keep priority on ties."""
    ordered = sorted(evidence, key=lambda i: (i.score, i.source.value == "database"), reverse=True)
    return ordered[:limit]
