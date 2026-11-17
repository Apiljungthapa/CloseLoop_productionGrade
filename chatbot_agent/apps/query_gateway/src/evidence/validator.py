from shared.schemas.retrieval_result import RetrievedItem


def has_sufficient_evidence(evidence: list[RetrievedItem]) -> bool:
    """True when at least one non-empty item exists (weak ones are filtered earlier)."""
    return any(item.text.strip() for item in evidence)
