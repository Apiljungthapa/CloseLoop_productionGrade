from typing import Any

from pydantic import BaseModel, Field

from shared.constants.enums import EvidenceSource


class RetrievedItem(BaseModel):
    """One piece of evidence: a document chunk or a database result."""

    source: EvidenceSource = EvidenceSource.DOCUMENT
    text: str
    score: float = 0.0
    document_id: str | None = None
    title: str | None = None
    section: str | None = None
    chunk_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    semantic_score: float | None = None
    keyword_score: float | None = None


class RetrievalResult(BaseModel):
    query: str
    items: list[RetrievedItem] = Field(default_factory=list)
