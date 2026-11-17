from pydantic import BaseModel, Field

from shared.constants.enums import Route


class SourceRef(BaseModel):
    document_id: str | None = None
    title: str | None = None
    section: str | None = None
    score: float | None = None
    source: str = "document"


class Answer(BaseModel):
    question: str
    answer: str
    route: Route = Route.DOC
    sources: list[SourceRef] = Field(default_factory=list)
