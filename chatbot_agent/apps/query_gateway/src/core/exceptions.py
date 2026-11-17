class GatewayError(Exception):
    """Base for errors that map to an HTTP status."""

    status_code = 500
    public_message = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.public_message)
        self.detail = detail


class LLMUnavailable(GatewayError):
    status_code = 502
    public_message = "The answer service is temporarily unavailable."


class RetrievalUnavailable(GatewayError):
    status_code = 503
    public_message = "The knowledge store is temporarily unavailable."


class FeatureDisabled(GatewayError):
    status_code = 501
    public_message = "This capability is not enabled."


class RateLimited(LLMUnavailable):
    """Gemini quota or our own limiter says "not now"; the client should retry later."""

    status_code = 429
    public_message = "The AI service is busy. Please retry shortly."

    def __init__(self, detail: str | None = None, retry_after: int = 0) -> None:
        super().__init__(detail or f"Rate limited; retry in {retry_after}s")
        self.retry_after = max(0, int(retry_after))
        if self.retry_after:
            self.public_message = f"The AI service is busy. Please retry in about {self.retry_after} seconds."
