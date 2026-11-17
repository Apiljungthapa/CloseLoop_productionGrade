"""HTTP request/response models."""
from pydantic import BaseModel, Field

from shared.schemas.answer import SourceRef
from shared.schemas.retrieval_result import RetrievedItem


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class AskResponse(BaseModel):
    question: str
    answer: str
    route: str
    sources: list[SourceRef]


class RetrieveResponse(BaseModel):
    query: str
    total_matches: int
    results: list[RetrievedItem]


class WorkflowRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant_id: str | None = Field(default=None, max_length=128, description="Required for database questions")
    top_k: int = Field(default=5, ge=1, le=20)
    max_rows: int = Field(default=50, ge=1, le=200)
    session_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{8,64}$",
        description="Client-generated random id (e.g. a UUID) that ties a chat together. Omit for a one-off question.",
    )


class WorkflowResponse(BaseModel):
    question: str
    answer: str
    route: str
    route_reason: str = ""
    route_source: str = "rules"
    needs_clarification: bool = False
    sources: list[SourceRef] = Field(default_factory=list)
    sql: str | None = None
    row_count: int | None = None
    interpretation: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    session_id: str | None = None
    rewritten_question: str | None = None  # the standalone question used when a follow-up was resolved
