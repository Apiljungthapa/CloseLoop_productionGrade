"""Conversation memory: the last few question/answer turns of one chat, kept in Redis.

Why: follow-ups such as "and last month?" only make sense with the previous turns. The workflow
uses this history to rewrite a follow-up into a complete question *before* anything else runs, so the
router, document search, SQL planner and caches all keep seeing ordinary standalone questions.

Rules this module enforces:
  * Fail-open: no Redis (or any Redis error) means "no memory": the chat keeps working, one question
    at a time, and the caller is told through a warning.
  * Isolated: the key contains the tenant, so one tenant can never read another's conversation.
  * Bounded and short-lived: newest N turns only, each text capped, expiry refreshed on every turn.
  * Text only: questions and answers as shown to the user. Database rows and SQL are never stored.
  * Memory is context, not authority: whatever is loaded still goes through the normal guardrail,
    tenant scoping and SQL validation like any other text.
"""
import time
from typing import Any

from loguru import logger

from core import cache as cache_layer
from core.config import get_settings

LAYER = "chat"
MEMORY_UNAVAILABLE = "Conversation memory is unavailable right now; this question was answered on its own."


def _key(tenant_id: str | None, session_id: str) -> str:
    return cache_layer.Cache.make_key(LAYER, session_id, tenant=tenant_id)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load_history(tenant_id: str | None, session_id: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(turns, warnings)``. Turns are oldest first: ``{"q": ..., "a": ..., "ts": ...}``."""
    settings = get_settings()
    if not session_id or not settings.CHAT_MEMORY_ENABLED:
        return [], []
    turns = cache_layer.get_cache().list_get(LAYER, _key(tenant_id, session_id))
    if turns is None:
        return [], [MEMORY_UNAVAILABLE]
    valid = [t for t in turns if isinstance(t, dict) and isinstance(t.get("q"), str) and isinstance(t.get("a"), str)]
    return valid[-settings.CHAT_MEMORY_TURNS :], []


def save_turn(tenant_id: str | None, session_id: str | None, question: str, answer: str) -> bool:
    """Append a finished turn. Never raises: losing a turn must not fail an answer already given."""
    settings = get_settings()
    if not session_id or not settings.CHAT_MEMORY_ENABLED or not question.strip() or not answer.strip():
        return False
    turn = {
        "q": _clip(question, settings.CHAT_MEMORY_MAX_QUESTION_CHARS),
        "a": _clip(answer, settings.CHAT_MEMORY_MAX_ANSWER_CHARS),
        "ts": int(time.time()),
    }
    try:
        return cache_layer.get_cache().list_push(
            LAYER, _key(tenant_id, session_id), turn, settings.CHAT_MEMORY_TURNS, settings.CHAT_MEMORY_TTL_S
        )
    except Exception:  # pragma: no cover - defensive; Cache already fails open
        logger.exception("Could not save conversation turn")
        return False


def clear_session(tenant_id: str | None, session_id: str) -> None:
    """Forget a conversation (the user's "new chat" / "delete my data")."""
    cache_layer.get_cache().delete(LAYER, _key(tenant_id, session_id))
