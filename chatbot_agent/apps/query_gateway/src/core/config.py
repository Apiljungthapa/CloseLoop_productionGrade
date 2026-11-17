"""Gateway settings. Real env vars win over the repo-root .env."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_parents = Path(__file__).resolve().parents
REPO_ROOT = _parents[min(4, len(_parents) - 1)]  # apps/query_gateway/src/core -> repo root (shallower in Docker)


class Settings(BaseSettings):
    # LLM
    # Which provider answers: gemini | openai | anthropic | openai_compatible.
    # LLM_FALLBACKS (comma separated, tried in order) takes over when the primary is rate-limited or down.
    LLM_PROVIDER: str = "gemini"
    LLM_FALLBACKS: str = ""
    LLM_TIMEOUT_S: int = 60

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""  # required to enable OpenAI (no default: model names retire)
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"

    # Any OpenAI-compatible server: Ollama, vLLM, LM Studio, OpenRouter, Groq, Azure OpenAI proxy...
    LLM_COMPAT_BASE_URL: str = ""  # e.g. http://host.docker.internal:11434/v1
    LLM_COMPAT_API_KEY: str = ""  # optional for local servers
    LLM_COMPAT_MODEL: str = ""

    # Vector store (must match what ingestion wrote)
    QDRANT_CLUSTER_ENDPOINT: str = ""
    QDRANT_API_KEY: str = ""
    QDRANT_LOCAL_PATH: str = "./qdrant_storage"
    QDRANT_COLLECTION: str = "rag_documents"
    QDRANT_VECTOR_SIZE: int = 512
    EMBEDDING_MODEL: str = "gemini-embedding-001"

    # Retrieval
    MIN_RETRIEVAL_SCORE: float = 0.25
    SEMANTIC_WEIGHT: float = 0.75

    # Relational database and SQL-planning API
    DB_ENABLED: bool = False
    DB_URL: str = ""
    SQL_PLANNER_API_ENABLED: bool = False
    SQL_PLANNER_API_KEY: str = ""
    SQL_QUERY_EXECUTION_ENABLED: bool = False
    SQL_LLM_BUDGET_S: int = Field(default=60, gt=0)
    DB_QUERY_TIMEOUT_MS: int = Field(default=5000, gt=0)

    # LangGraph workflow
    WORKFLOW_LLM_ROUTING: bool = True  # False = keyword rules only (no extra model call)

    # Per-provider protection (shared across workers through Redis). 0 = no client-side limit.
    GEMINI_MAX_RPM: int = 0
    OPENAI_MAX_RPM: int = 0
    ANTHROPIC_MAX_RPM: int = 0
    LLM_COMPAT_MAX_RPM: int = 0
    LLM_RATE_WAIT_S: int = 20  # wait up to this long for a free slot before failing over / answering 429

    # Redis cache (fail-open: the gateway works without it)
    CACHE_ENABLED: bool = True
    REDIS_URL: str = ""  # e.g. redis://:password@redis:6379/0 ; empty disables caching
    CACHE_TIMEOUT_S: float = 0.25
    CACHE_TTL_LLM_S: int = 6 * 3600  # deterministic router/guardrail replies
    CACHE_TTL_PLAN_S: int = 3600  # question -> SQL drafts
    CACHE_TTL_DOCS_S: int = 3600  # document search results
    CACHE_TTL_SCHEMA_S: int = 600  # database schema catalog

    # Conversation memory (needs Redis; without it every request is answered on its own)
    CHAT_MEMORY_ENABLED: bool = True
    CHAT_MEMORY_TURNS: int = Field(default=8, ge=1, le=50)  # question/answer pairs kept per conversation
    CHAT_MEMORY_TTL_S: int = Field(default=1800, gt=0)  # idle time before a conversation is forgotten
    CHAT_MEMORY_MAX_QUESTION_CHARS: int = 600
    CHAT_MEMORY_MAX_ANSWER_CHARS: int = 1500

    # Logging
    LOG_LEVEL: str = "INFO"  # DEBUG shows guardrail/planner internals; use INFO or WARNING in production

    # API
    API_AUTH_KEY: str = ""
    ALLOWED_ORIGINS: str = "*"

    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
