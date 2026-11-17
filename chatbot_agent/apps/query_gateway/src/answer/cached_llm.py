"""Reuse deterministic model replies (temperature 0) instead of paying for the same call twice."""
from typing import Callable

from core import cache as cache_layer
from core.cache import normalize_text
from core.config import get_settings


def cached_generate(
    generate: Callable[..., str],
    system_prompt: str,
    user_prompt: str,
    *,
    layer: str,
    model: str | None = None,
    validate: Callable[[str], bool] | None = None,
) -> str:
    """Call ``generate(system, user, temperature=0)`` through the cache.

    The key covers model + system prompt + normalized user prompt, so editing a prompt or switching
    model automatically starts fresh. Replies are stored only when ``validate`` accepts them, so a
    malformed answer is never replayed. Only use this for temperature-0, tenant-free prompts.
    """
    settings = get_settings()
    cache = cache_layer.get_cache()
    key = cache.make_key(layer, model or "default", system_prompt, normalize_text(user_prompt))
    hit = cache.get_json(layer, key)
    if isinstance(hit, str):
        return hit
    reply = generate(system_prompt, user_prompt, temperature=0)
    if validate is None or validate(reply):
        cache.set_json(layer, key, reply, settings.CACHE_TTL_LLM_S)
    return reply
