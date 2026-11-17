"""Trusted bridge between CloseLoop users and the chatbot's query gateway.

The gateway answers from a tenant's database rows and documents, and it takes the tenant from the
request body. It therefore must never be reachable by a browser. This module is the only caller:

  * the tenant is the logged-in user's own `tenant_id` (the router passes it in, read from the
    database — never from the request body), so a user can only ever query their tenant;
  * the shared keys (SQL planner key, gateway key) come from the server environment;
  * each conversation id is bound to the user, so two users of one tenant never share memory;
  * what is relayed to the browser is whitelisted: the generated SQL, routing internals and the
    gateway's own session id never leave the server.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

STREAM_PATH = "/workflow/stream"
SESSION_PATH = "/workflow/session"
MAX_ROWS = 50
TOP_K = 5


class ChatbotNotConfigured(Exception):
    """CHATBOT_ENABLED is off, or the URL / SQL key is missing."""


class ChatbotUnavailable(Exception):
    """The gateway is unreachable or answered with an unexpected status."""


class ChatbotBusy(Exception):
    """Too many questions: per-user rate, or too many streams in flight."""

    def __init__(self, retry_after: int = 5) -> None:
        super().__init__("busy")
        self.retry_after = retry_after


def is_configured() -> bool:
    return bool(
        settings.CHATBOT_ENABLED
        and settings.CHATBOT_URL.strip()
        and settings.CHATBOT_SQL_API_KEY
    )


def gateway_session_id(user_id: str, client_session_id: Optional[str]) -> Optional[str]:
    """Conversation id sent to the gateway. Derived from the user, so the same browser session id
    used by someone else maps to a different conversation. 32 hex chars fits the gateway's
    `[A-Za-z0-9_-]{8,64}` rule. None = a one-off question with no memory."""
    if not client_session_id:
        return None
    digest = hashlib.sha256(f"{user_id}:{client_session_id}".encode("utf-8")).hexdigest()
    return digest[:32]


def _headers() -> Dict[str, str]:
    headers = {"X-SQL-API-Key": settings.CHATBOT_SQL_API_KEY}
    if settings.CHATBOT_API_KEY:
        headers["X-API-Key"] = settings.CHATBOT_API_KEY
    return headers


# ---------------------------------------------------------------------------
# Limits (in-process: the backend runs a single worker — see docker-compose.prod.yml)
# ---------------------------------------------------------------------------
_hits: Dict[str, Deque[float]] = {}
_active_total = 0
_active_by_user: Dict[str, int] = {}
RATE_WINDOW_SECONDS = 60.0


def reset_limits() -> None:
    """Test helper."""
    global _active_total
    _hits.clear()
    _active_by_user.clear()
    _active_total = 0


def acquire(user_id: str) -> None:
    """Count one question against the user's rate and the concurrency caps, or raise ChatbotBusy.
    Pair every successful call with `release(user_id)`."""
    global _active_total
    now = time.monotonic()
    window = _hits.setdefault(user_id, deque())
    while window and now - window[0] > RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= settings.CHATBOT_RATE_LIMIT_PER_MINUTE:
        retry = int(RATE_WINDOW_SECONDS - (now - window[0])) + 1
        raise ChatbotBusy(retry)
    if (
        _active_total >= settings.CHATBOT_MAX_CONCURRENT
        or _active_by_user.get(user_id, 0) >= settings.CHATBOT_MAX_CONCURRENT_PER_USER
    ):
        raise ChatbotBusy(5)
    window.append(now)
    _active_total += 1
    _active_by_user[user_id] = _active_by_user.get(user_id, 0) + 1
    if len(_hits) > 5000:  # forget users who have been quiet for a full window
        for uid in [u for u, w in _hits.items() if not w or now - w[-1] > RATE_WINDOW_SECONDS]:
            _hits.pop(uid, None)


def release(user_id: str) -> None:
    global _active_total
    _active_total = max(0, _active_total - 1)
    remaining = _active_by_user.get(user_id, 0) - 1
    if remaining > 0:
        _active_by_user[user_id] = remaining
    else:
        _active_by_user.pop(user_id, None)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
class Upstream:
    """An open streaming response from the gateway. Owns the user's concurrency slot: `aclose()` is
    idempotent and frees the connection and the slot exactly once, however the stream ends."""

    def __init__(self, client: httpx.AsyncClient, response: httpx.Response, user_id: str) -> None:
        self.client = client
        self.response = response
        self.user_id = user_id
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.response.aclose()
        finally:
            try:
                await self.client.aclose()
            finally:
                release(self.user_id)


async def open_stream(
    *, question: str, tenant_id: str, user_id: str, client_session_id: Optional[str]
) -> Upstream:
    """POST the question for `tenant_id` to the gateway and return the open SSE response.

    Raises ChatbotNotConfigured / ChatbotBusy / ChatbotUnavailable before any byte is streamed,
    so the router can answer with a proper HTTP status. On success the returned Upstream owns the
    user's rate/concurrency slot (see `acquire`)."""
    if not is_configured():
        raise ChatbotNotConfigured()
    if not tenant_id:
        raise ChatbotNotConfigured("tenant_id is required")
    acquire(user_id)  # ChatbotBusy if over the per-minute rate or the concurrency caps
    try:
        return await _open(question, tenant_id, user_id, client_session_id)
    except BaseException:
        release(user_id)
        raise


async def _open(
    question: str, tenant_id: str, user_id: str, client_session_id: Optional[str]
) -> Upstream:

    body: Dict[str, Any] = {
        "question": question,
        "tenant_id": tenant_id,
        "max_rows": MAX_ROWS,
        "top_k": TOP_K,
    }
    sid = gateway_session_id(user_id, client_session_id)
    if sid:
        body["session_id"] = sid

    client = httpx.AsyncClient(
        base_url=settings.CHATBOT_URL.rstrip("/"),
        timeout=httpx.Timeout(
            connect=5.0, read=float(settings.CHATBOT_TIMEOUT_SECONDS), write=10.0, pool=5.0
        ),
    )
    try:
        request = client.build_request("POST", STREAM_PATH, json=body, headers=_headers())
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("chatbot gateway unreachable: %s", type(exc).__name__)
        raise ChatbotUnavailable("gateway unreachable") from exc

    if response.status_code != 200:
        status = response.status_code
        retry_after = response.headers.get("Retry-After")
        await response.aclose()
        await client.aclose()
        logger.warning("chatbot gateway answered %s", status)
        if status == 429:
            raise ChatbotBusy(int(retry_after) if retry_after and retry_after.isdigit() else 10)
        raise ChatbotUnavailable(f"gateway returned {status}")
    return Upstream(client, response, user_id)


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _pick(data: Dict[str, Any], keys: tuple[str, ...]) -> Dict[str, Any]:
    return {k: data[k] for k in keys if k in data}


def sanitize_block(block: str) -> Optional[str]:
    """One raw SSE block from the gateway -> the block the browser may see, or None to drop it.

    Whitelist, not blacklist: unknown events and unknown fields are dropped. In particular the
    generated SQL (`meta.sql`), routing reasons, per-step traces and the gateway's session id stay
    on the server."""
    lines = [ln for ln in block.split("\n") if ln != ""]
    if not lines:
        return None
    if all(ln.startswith(":") for ln in lines):
        return "\n".join(lines) + "\n\n"  # heartbeat / "connected" comment keeps proxies open

    event, data_str = "message", ""
    for ln in lines:
        if ln.startswith("event:"):
            event = ln[6:].strip()
        elif ln.startswith("data:"):
            data_str += ln[5:].lstrip()
    try:
        data = json.loads(data_str) if data_str else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    if event == "token":
        return _sse("token", {"text": str(data.get("text", ""))})
    if event == "status":
        return _sse("status", _pick(data, ("step", "message")))
    if event == "route":
        return _sse("route", _pick(data, ("route",)))
    if event == "rewrite":
        return _sse("rewrite", _pick(data, ("question",)))
    if event == "meta":
        return _sse("meta", _pick(data, ("interpretation", "assumptions", "row_count")))
    if event == "sources":
        items = data.get("items")
        clean = [
            _pick(i, ("document_id", "title", "section", "score"))
            for i in (items if isinstance(items, list) else [])
            if isinstance(i, dict)
        ]
        return _sse("sources", {"items": clean})
    if event == "done":
        return _sse("done", _pick(data, ("route", "needs_clarification", "warnings", "elapsed_ms")))
    if event == "error":
        return _sse("error", _pick(data, ("message", "status", "retry_after")))
    return None


async def relay(upstream: Upstream) -> AsyncIterator[str]:
    """Yield sanitized SSE text. Always closes the upstream and frees the user's slot, including when
    the browser disconnects mid-answer (the generator is then closed, which stops the gateway too)."""
    try:
        buffer = ""
        async for chunk in upstream.response.aiter_text():
            buffer += chunk.replace("\r\n", "\n")
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                out = sanitize_block(block)
                if out:
                    yield out
        if buffer.strip():
            out = sanitize_block(buffer)
            if out:
                yield out
    except httpx.HTTPError as exc:
        logger.warning("chatbot stream interrupted: %s", type(exc).__name__)
        yield _sse("error", {"message": "The assistant stopped responding. Please try again.", "status": 502})
    finally:
        await upstream.aclose()


async def clear_session(*, tenant_id: str, user_id: str, client_session_id: str) -> None:
    """Forget one conversation ("new chat"). Best effort: a failure here must not surface."""
    if not is_configured():
        return
    sid = gateway_session_id(user_id, client_session_id)
    if not sid:
        return
    try:
        async with httpx.AsyncClient(
            base_url=settings.CHATBOT_URL.rstrip("/"), timeout=httpx.Timeout(5.0)
        ) as client:
            await client.delete(
                f"{SESSION_PATH}/{sid}", params={"tenant_id": tenant_id}, headers=_headers()
            )
    except httpx.HTTPError as exc:
        logger.info("could not clear chatbot session: %s", type(exc).__name__)
