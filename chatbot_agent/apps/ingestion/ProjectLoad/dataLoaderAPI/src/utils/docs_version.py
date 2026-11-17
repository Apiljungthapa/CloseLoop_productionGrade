"""Tell the query gateway that stored documents changed so its cached searches expire.

Fail-open: if Redis is unreachable the upload/delete still succeeds; the gateway's cached
searches then simply expire on their own TTL (default 1 hour).
"""
import os
from functools import lru_cache

from loguru import logger

# Must match DOCS_VERSION_KEY in apps/query_gateway/src/core/cache.py
DOCS_VERSION_KEY = "cl:v1:docs_version"


@lru_cache
def _client():
    url = os.environ.get("REDIS_URL", "").strip()
    if not url or os.environ.get("CACHE_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
        return None
    import redis

    return redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)


def bump_docs_version() -> None:
    client = _client()
    if client is None:
        return
    try:
        client.incr(DOCS_VERSION_KEY)
    except Exception as error:
        logger.warning(f"Could not invalidate the gateway's document cache: {error}")
