from pydantic import BaseModel, Field


class UserQuery(BaseModel):
    """A question as it enters the gateway."""

    text: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    document_id: str | None = Field(default=None, description="Restrict document search to one document")
