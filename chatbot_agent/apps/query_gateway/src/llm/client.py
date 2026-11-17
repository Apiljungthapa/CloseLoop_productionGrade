"""The one LLM object the rest of the gateway uses.

``LLM_PROVIDER`` picks the primary vendor and its model; ``LLM_FALLBACKS`` lists the vendors to try, in
order, when the primary is rate-limited, out of quota, rejecting our key, or down. A vendor that fails is
skipped for a while (cooldown, shared through Redis and also kept in-process so it works without Redis).
"""
import time
from typing import Any, Iterator

from loguru import logger

from core.config import Settings
from core.exceptions import LLMUnavailable, RateLimited
from llm.providers import ALIASES, PROVIDERS, BaseProvider, ProviderFailure

BREAKER_COOLDOWN_S = 30  # after a vendor exhausts its retries, send traffic elsewhere for this long


def canonical_name(name: str) -> str:
    key = name.strip().lower().replace(" ", "_")
    return ALIASES.get(key, key.replace("-", "_"))


def build_chain(settings: Settings) -> tuple[list[BaseProvider], list[str]]:
    """Providers to try, in order, plus human-readable configuration warnings."""
    requested = [settings.LLM_PROVIDER, *[n for n in settings.LLM_FALLBACKS.split(",") if n.strip()]]
    chain: list[BaseProvider] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        name = canonical_name(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        provider_class = PROVIDERS.get(name)
        if provider_class is None:
            warnings.append(f"Unknown LLM provider {raw!r}; choose from {sorted(PROVIDERS)}.")
            continue
        provider = provider_class.from_settings(settings)
        if not provider.configured:
            warnings.append(f"LLM provider {name!r} is listed but not configured (missing API key or model); skipped.")
            continue
        chain.append(provider)
    return chain, warnings


class LLMClient:
    def __init__(self, settings: Settings, total_timeout_s: int | None = None) -> None:
        self.settings = settings
        self.deadline = time.monotonic() + total_timeout_s if total_timeout_s is not None else None
        self.chain, warnings = build_chain(settings)
        for warning in warnings:
            logger.warning(warning)
        self._cooldown_until: dict[str, float] = {}
        if self.chain:
            logger.info("LLM providers (primary first): {}", " -> ".join(p.label for p in self.chain))
        else:
            logger.error("No LLM provider is configured: set LLM_PROVIDER and that provider's API key/model.")

    # ---- identity ------------------------------------------------------------------------
    @property
    def model_id(self) -> str:
        """Stable id of the PRIMARY provider/model; part of cache keys so a switch starts fresh."""
        return self.chain[0].label if self.chain else "none"

    def describe(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "provider": p.name,
                "model": p.model,
                "role": "primary" if i == 0 else "fallback",
                "state": "cooling_down" if self._cooldown_until.get(p.name, 0) > now else "ready",
            }
            for i, p in enumerate(self.chain)
        ]

    # ---- calls ---------------------------------------------------------------------------
    def _cooling(self, provider: BaseProvider) -> int:
        return max(0, int(self._cooldown_until.get(provider.name, 0) - time.monotonic() + 0.999))

    def _record_failure(
        self, provider: BaseProvider, error: LLMUnavailable, failures: list, index: int
    ) -> None:
        """Remember why a provider failed, park it if appropriate, and log the failover."""
        if isinstance(error, RateLimited):
            cooldown = error.retry_after
        elif isinstance(error, ProviderFailure):
            cooldown = error.cooldown_s
        else:
            cooldown = BREAKER_COOLDOWN_S
        failures.append((provider, error))
        if cooldown:
            self._cooldown_until[provider.name] = time.monotonic() + cooldown
        following = self.chain[index + 1].label if index + 1 < len(self.chain) else None
        logger.warning(
            "LLM provider {} failed ({}); {}",
            provider.label,
            error,
            f"trying {following}" if following else "no more providers",
        )

    def _all_failed(self, failures: list) -> LLMUnavailable:
        if all(isinstance(error, RateLimited) for _, error in failures):
            return RateLimited(
                "All LLM providers are rate-limited",
                retry_after=min(error.retry_after for _, error in failures),
            )
        return LLMUnavailable(
            "All LLM providers failed: " + "; ".join(f"{p.name}: {error}" for p, error in failures)
        )

    def generate(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        if not self.chain:
            raise LLMUnavailable("No LLM provider is configured.")
        failures: list[tuple[BaseProvider, LLMUnavailable]] = []
        for index, provider in enumerate(self.chain):
            remaining = self._cooling(provider)
            if remaining:
                failures.append((provider, RateLimited(f"{provider.name} cooling down", retry_after=remaining)))
                continue
            try:
                return provider.generate(system_prompt, user_prompt, temperature, self.deadline)
            except LLMUnavailable as error:  # RateLimited and ProviderFailure are subclasses
                self._record_failure(provider, error, failures, index)
        raise self._all_failed(failures)

    def stream_generate(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> Iterator[str]:
        """Yield the answer piece by piece.

        Failover is only possible BEFORE the first piece: once text has been sent to the user, switching
        to another model would splice two different answers together, so a later failure is raised.
        Closing this generator closes the vendor connection (stops generation and billing).
        """
        if not self.chain:
            raise LLMUnavailable("No LLM provider is configured.")
        failures: list[tuple[BaseProvider, LLMUnavailable]] = []
        for index, provider in enumerate(self.chain):
            remaining = self._cooling(provider)
            if remaining:
                failures.append((provider, RateLimited(f"{provider.name} cooling down", retry_after=remaining)))
                continue
            started = False
            try:
                for piece in provider.stream(system_prompt, user_prompt, temperature, self.deadline):
                    started = True
                    yield piece
                return
            except LLMUnavailable as error:
                if started:
                    self._record_failure(provider, error, failures, len(self.chain))
                    raise
                self._record_failure(provider, error, failures, index)
        raise self._all_failed(failures)
