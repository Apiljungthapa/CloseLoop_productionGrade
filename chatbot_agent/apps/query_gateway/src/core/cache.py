"""Fail-open Redis cache, rate limiter and cooldown flags.

Rules this module enforces for every caller:
  * Redis is an accelerator, never a dependency. Any Redis error returns the "miss" answer and
    the cache is skipped for a few seconds (circuit breaker), so a dead Redis costs nothing.
  * Keys are namespaced (``cl:v1:<layer>:<tenant>:<hash>``). Tenant-owned data MUST pass ``tenant=``
    so one tenant can never be served another tenant's entry. Arguments are hashed, so secrets or
    long prompts never appear in key names.
  * Values are JSON and always carry a TTL.
"""
import hashlib
import json
import threading
import time
from functools import lru_cache
from typing import Any, Callable

from loguru import logger

from core.config import get_settings

KEY_PREFIX = "cl:v1"
# Bumped by the ingestion service whenever documents are added/changed/deleted (must match its copy).
DOCS_VERSION_KEY = f"{KEY_PREFIX}:docs_version"
BREAKER_SECONDS = 10.0


def normalize_text(text: str) -> str:
    """Case/whitespace-insensitive form of a user question, used only inside cache keys."""
    return " ".join(text.lower().split())


class MemoryBackend:
    """In-process stand-in with the slice of the redis-py API used here (tests, local experiments)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def _alive(self, key: str) -> bool:
        item = self._data.get(key)
        if item is None:
            return False
        if item[1] is not None and item[1] <= time.monotonic():
            del self._data[key]
            return False
        return True

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data[key][0] if self._alive(key) else None

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        with self._lock:
            self._data[key] = (value, time.monotonic() + ex if ex else None)
        return True

    def incr(self, key: str) -> int:
        with self._lock:
            current = int(self._data[key][0]) if self._alive(key) else 0
            expires = self._data[key][1] if key in self._data else None
            self._data[key] = (str(current + 1), expires)
            return current + 1

    def expire(self, key: str, seconds: int) -> bool:
        with self._lock:
            if not self._alive(key):
                return False
            self._data[key] = (self._data[key][0], time.monotonic() + seconds)
            return True

    def ttl(self, key: str) -> int:
        with self._lock:
            if not self._alive(key):
                return -2
            expires = self._data[key][1]
            return -1 if expires is None else max(0, int(expires - time.monotonic() + 0.999))

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._data.pop(key, None) is not None else 0

    def ping(self) -> bool:
        return True

    # lists (used for conversation memory)
    def rpush(self, key: str, value: str) -> int:
        with self._lock:
            items = self._data[key][0] if self._alive(key) else []
            expires = self._data[key][1] if key in self._data else None
            items.append(value)
            self._data[key] = (items, expires)
            return len(items)

    def ltrim(self, key: str, start: int, stop: int) -> bool:
        with self._lock:
            if self._alive(key):
                items = self._data[key][0]
                kept = items[start:] if stop == -1 else items[start : stop + 1]
                self._data[key] = (kept, self._data[key][1])
            return True

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        with self._lock:
            if not self._alive(key):
                return []
            items = self._data[key][0]
            return list(items[start:] if stop == -1 else items[start : stop + 1])

    def pipeline(self, transaction: bool = True) -> "MemoryBackend._Pipeline":
        return MemoryBackend._Pipeline(self)

    class _Pipeline:
        def __init__(self, backend: "MemoryBackend") -> None:
            self._backend, self._ops = backend, []

        def __getattr__(self, name: str):
            def queue(*args, **kwargs):
                self._ops.append((name, args, kwargs))
                return self

            return queue

        def execute(self) -> list:
            return [getattr(self._backend, name)(*args, **kwargs) for name, args, kwargs in self._ops]


class Cache:
    def __init__(self, backend: Any | None) -> None:
        self._backend = backend
        self._down_until = 0.0
        self._last_warning = 0.0
        self._lock = threading.Lock()
        self._counters: dict[str, dict[str, int]] = {}

    # ---- plumbing ---------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._backend is not None

    def _count(self, layer: str, outcome: str) -> None:
        with self._lock:
            bucket = self._counters.setdefault(layer, {"hit": 0, "miss": 0, "set": 0, "error": 0})
            bucket[outcome] += 1

    def _call(self, layer: str, operation: Callable[[Any], Any], default: Any = None) -> Any:
        if self._backend is None or time.monotonic() < self._down_until:
            return default
        try:
            return operation(self._backend)
        except Exception as error:  # redis errors, timeouts, bad URL... never break the request
            now = time.monotonic()
            self._down_until = now + BREAKER_SECONDS
            self._count(layer, "error")
            if now - self._last_warning > 30:
                self._last_warning = now
                logger.warning("Cache unavailable, continuing without it ({}): {}", layer, error)
            return default

    @staticmethod
    def make_key(layer: str, *parts: Any, tenant: str | None = None) -> str:
        digest = hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:40]
        owner = hashlib.sha256(tenant.encode()).hexdigest()[:16] if tenant else "-"
        return f"{KEY_PREFIX}:{layer}:{owner}:{digest}"

    # ---- values -----------------------------------------------------------------------
    def get_json(self, layer: str, key: str) -> Any | None:
        raw = self._call(layer, lambda b: b.get(key))
        if raw is None:
            if self.enabled:
                self._count(layer, "miss")
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        self._count(layer, "hit")
        logger.debug("cache hit layer={}", layer)
        return value

    def set_json(self, layer: str, key: str, value: Any, ttl: int) -> None:
        if not self.enabled or ttl <= 0:
            return
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError):
            return
        if self._call(layer, lambda b: b.set(key, payload, ex=ttl)):
            self._count(layer, "set")

    def delete(self, layer: str, key: str) -> None:
        self._call(layer, lambda b: b.delete(key))

    # ---- lists: append-only logs with a size cap and an idle expiry ----------------------------
    def list_push(self, layer: str, key: str, value: Any, max_len: int, ttl: int) -> bool:
        """Append one JSON item, keep only the newest ``max_len``, and refresh the expiry - atomically,
        so two concurrent requests in one conversation cannot lose each other's turn."""
        if not self.enabled or ttl <= 0 or max_len <= 0:
            return False
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError):
            return False

        def operation(backend: Any) -> bool:
            pipe = backend.pipeline(transaction=True)
            pipe.rpush(key, payload)
            pipe.ltrim(key, -max_len, -1)
            pipe.expire(key, ttl)
            pipe.execute()
            return True

        if self._call(layer, operation, False):
            self._count(layer, "set")
            return True
        return False

    def list_get(self, layer: str, key: str) -> list[Any] | None:
        """All items, oldest first. ``[]`` = nothing stored; ``None`` = the cache is unavailable."""
        raw = self._call(layer, lambda b: b.lrange(key, 0, -1))
        if raw is None:
            return None
        items: list[Any] = []
        for entry in raw:
            try:
                items.append(json.loads(entry))
            except ValueError:
                continue  # skip a corrupt entry instead of losing the whole conversation
        self._count(layer, "hit" if items else "miss")
        return items

    # ---- counters / versions ---------------------------------------------------------
    def get_int(self, key: str) -> int:
        raw = self._call("version", lambda b: b.get(key))
        try:
            return int(raw) if raw is not None else 0
        except ValueError:
            return 0

    def incr(self, key: str) -> int | None:
        return self._call("version", lambda b: b.incr(key))

    # ---- limiter and cooldown -----------------------------------------------------------
    def rate_limit(self, name: str, limit: int, window_s: int = 60) -> tuple[bool, int]:
        """Fixed-window limiter shared by every worker. Returns (allowed, seconds_until_reset)."""
        if limit <= 0:
            return True, 0
        now = time.time()
        window = int(now // window_s)
        key = f"{KEY_PREFIX}:rl:{name}:{window}"

        def operation(backend: Any) -> int:
            count = backend.incr(key)
            if count == 1:
                backend.expire(key, window_s + 1)
            return count

        count = self._call("ratelimit", operation)
        if count is None:  # fail open
            return True, 0
        return count <= limit, max(1, int(window_s - (now % window_s)))

    def set_cooldown(self, name: str, seconds: int) -> None:
        if seconds > 0:
            self._call("cooldown", lambda b: b.set(f"{KEY_PREFIX}:cooldown:{name}", "1", ex=int(seconds)))

    def cooldown_remaining(self, name: str) -> int:
        ttl = self._call("cooldown", lambda b: b.ttl(f"{KEY_PREFIX}:cooldown:{name}"), -2)
        return ttl if isinstance(ttl, int) and ttl > 0 else 0

    # ---- introspection ------------------------------------------------------------------
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if time.monotonic() < self._down_until:
            return "down"
        return "up" if self._call("ping", lambda b: b.ping(), False) else "down"

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {layer: dict(counts) for layer, counts in self._counters.items()}


@lru_cache
def get_cache() -> Cache:
    settings = get_settings()
    if not (settings.CACHE_ENABLED and settings.REDIS_URL):
        return Cache(None)
    import redis

    client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=settings.CACHE_TIMEOUT_S,
        socket_timeout=settings.CACHE_TIMEOUT_S,
        health_check_interval=30,
    )
    return Cache(client)
