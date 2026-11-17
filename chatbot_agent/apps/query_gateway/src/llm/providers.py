"""One adapter per LLM vendor. They differ only in request/response shape; the retry,
rate-limit and cooldown behaviour lives once, in ``BaseProvider.generate``.

Supported: Gemini, OpenAI, Anthropic (Claude) and any OpenAI-compatible server.
No vendor SDKs: plain HTTPS through ``requests``, which keeps images small and behaviour uniform.
"""
import json
import re
import time
from dataclasses import dataclass
from typing import Iterator

import requests
from loguru import logger

from core import cache as cache_layer
from core.config import Settings
from core.exceptions import LLMUnavailable, RateLimited

RETRYABLE = (429, 500, 502, 503, 504, 529)  # 529 = Anthropic "overloaded"
COOLDOWN_MAX_S = 3600  # even for a daily quota, probe again after an hour
SHORT_RETRY_S = 3  # a 429 that clears this fast is worth one ordinary retry
AUTH_COOLDOWN_S = 600  # bad key / denied project / unknown model: stop hammering for 10 minutes
MAX_OUTPUT_TOKENS = 2048


class ProviderFailure(LLMUnavailable):
    """The provider cannot serve this request. ``cooldown_s`` > 0 means "skip it for a while"."""

    def __init__(self, detail: str, cooldown_s: int = 0) -> None:
        super().__init__(detail)
        self.cooldown_s = cooldown_s


@dataclass
class Reply:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def _duration_s(value: str | None) -> int:
    """'30' / '1.5s' / '6m0s' / '250ms' -> whole seconds (rounded up)."""
    if not value:
        return 0
    value = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return int(float(value) + 0.999)
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value):
        total += float(amount) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return int(total + 0.999) if total else 0


def _retry_delay_s(response: requests.Response) -> int:
    """Gemini: seconds it asks us to wait (RetryInfo / "retry in 1h2m3.4s"), or 0 when unknown."""
    try:
        text = response.text
    except Exception:
        return 0
    info = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', text)
    if info:
        return int(float(info.group(1)) + 0.999)
    match = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", text)
    if match and any(match.groups()):
        hours, minutes, seconds = (float(g or 0) for g in match.groups())
        return int(hours * 3600 + minutes * 60 + seconds + 0.999)
    return 0


class BaseProvider:
    name = "base"
    strict_completion = True  # a stream that stops without a "finished" marker is an error (truncated)

    def __init__(self, settings: Settings, model: str, api_key: str, max_rpm: int = 0) -> None:
        self.settings = settings
        self.model = model
        self.api_key = api_key
        self.max_rpm = max_rpm

    # ---- vendor-specific hooks ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def build_request(self, system: str, user: str, temperature: float | None) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def parse(self, data: dict) -> Reply:
        raise NotImplementedError

    def retry_delay_s(self, response: requests.Response) -> int:
        return _duration_s(getattr(response, "headers", {}).get("retry-after"))

    # ---- shared behaviour ------------------------------------------------------------------
    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"

    def _wait_for_slot(self, deadline: float | None) -> None:
        """Stop before calling the vendor when it is cooling down or our per-minute budget is spent."""
        cache = cache_layer.get_cache()
        remaining = cache.cooldown_remaining(self.name)
        if remaining > 0:
            raise RateLimited(f"{self.name} is cooling down for {remaining}s", retry_after=remaining)
        if self.max_rpm <= 0:
            return
        allowed, retry_after = cache.rate_limit(self.name, self.max_rpm, 60)
        if allowed:
            return
        budget = self.settings.LLM_RATE_WAIT_S
        if deadline is not None:
            budget = min(budget, deadline - time.monotonic())
        if retry_after > budget:
            raise RateLimited(f"{self.name} request budget for this minute is spent", retry_after=retry_after)
        time.sleep(retry_after)
        allowed, retry_after = cache.rate_limit(self.name, self.max_rpm, 60)
        if not allowed:
            raise RateLimited(f"{self.name} request budget for this minute is spent", retry_after=retry_after)

    def _handle_error_status(self, response: requests.Response, attempt: int) -> bool:
        """Raise for failures that retrying cannot fix; return True when the caller should retry."""
        status = response.status_code
        if status in (401, 403, 404):
            logger.error(f"{self.name} rejected the request ({status}): {response.text[:300]}")
            raise ProviderFailure(f"{self.name} returned {status}", cooldown_s=AUTH_COOLDOWN_S)
        if status not in RETRYABLE:
            logger.error(f"{self.name} error {status}: {response.text[:300]}")
            raise ProviderFailure(f"{self.name} returned {status}")  # request-specific: no cooldown
        logger.warning(f"{self.name} returned {status} (attempt {attempt + 1})")
        if status == 429:
            delay_s = self.retry_delay_s(response)
            if delay_s > SHORT_RETRY_S:
                # Quota/rate limit: retrying in 1-2 s only burns more requests. Tell every
                # worker to stop calling this vendor until the window reopens.
                wait = min(delay_s, COOLDOWN_MAX_S)
                cache_layer.get_cache().set_cooldown(self.name, wait)
                raise RateLimited(f"{self.name} returned 429", retry_after=wait)
        return attempt < 2

    def generate(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.3, deadline: float | None = None
    ) -> str:
        response = None
        include_temperature = True
        started = time.monotonic()
        for attempt in range(3):
            timeout = self.settings.LLM_TIMEOUT_S
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMUnavailable("The request exceeded its model time budget.")
                timeout = min(timeout, remaining)

            self._wait_for_slot(deadline)
            url, headers, body = self.build_request(
                system_prompt, user_prompt, temperature if include_temperature else None
            )
            retry = False
            try:
                response = requests.post(url, headers=headers, json=body, timeout=timeout)
            except requests.RequestException as error:
                logger.warning(f"{self.name} request failed (attempt {attempt + 1}): {error}")
                if attempt == 2:
                    raise LLMUnavailable(f"Could not reach {self.name}: {error}") from error
                retry = True
            else:
                status = response.status_code
                if status == 200:
                    break
                if status == 400 and include_temperature and "temperature" in response.text.lower():
                    include_temperature = False  # some models only accept their default sampling
                    continue
                retry = self._handle_error_status(response, attempt)

            if retry:
                delay = 2**attempt
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= delay:
                        if remaining > 0:
                            time.sleep(remaining)
                        raise LLMUnavailable("The request exceeded its model time budget.")
                time.sleep(delay)

        if response is None or response.status_code != 200:
            status = getattr(response, "status_code", "no response")
            logger.error(f"{self.name} error {status}: {getattr(response, 'text', '')[:300]}")
            raise LLMUnavailable(f"{self.name} returned {status}")

        try:
            reply = self.parse(response.json())
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMUnavailable(f"Unexpected {self.name} response.") from error
        logger.info(
            "LLM call ok provider={} model={} attempts={} {}ms input_tokens={} output_tokens={}",
            self.name,
            self.model,
            attempt + 1,
            round((time.monotonic() - started) * 1000),
            reply.input_tokens,
            reply.output_tokens,
        )
        return reply.text.strip()

    # ---- streaming -------------------------------------------------------------------------
    def build_stream_request(self, system: str, user: str, temperature: float | None) -> tuple[str, dict, dict]:
        return self.build_request(system, user, temperature)

    def parse_stream_event(self, data: dict, usage: dict) -> str | None:
        """Text carried by one streamed event (None for bookkeeping events). May record token usage."""
        raise NotImplementedError

    def stream(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.3, deadline: float | None = None
    ) -> Iterator[str]:
        """Yield the answer in pieces as the vendor produces it.

        Anything that goes wrong before the first piece raises (so the caller can fail over); closing
        the generator closes the HTTP connection, which stops the vendor generating (and billing).
        """
        include_temperature = True
        response = None
        for attempt in range(3):
            timeout = self.settings.LLM_TIMEOUT_S
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMUnavailable("The request exceeded its model time budget.")
                timeout = min(timeout, remaining)
            self._wait_for_slot(deadline)
            url, headers, body = self.build_stream_request(
                system_prompt, user_prompt, temperature if include_temperature else None
            )
            try:
                response = requests.post(url, headers=headers, json=body, timeout=(10, timeout), stream=True)
            except requests.RequestException as error:
                logger.warning(f"{self.name} stream request failed (attempt {attempt + 1}): {error}")
                if attempt == 2:
                    raise LLMUnavailable(f"Could not reach {self.name}: {error}") from error
                time.sleep(2**attempt)
                continue
            if response.status_code == 200:
                break
            try:
                if (
                    response.status_code == 400
                    and include_temperature
                    and "temperature" in response.text.lower()
                ):
                    include_temperature = False
                    continue
                retry = self._handle_error_status(response, attempt)
            finally:
                response.close()
            if not retry:
                break
            time.sleep(2**attempt)
        if response is None or response.status_code != 200:
            raise LLMUnavailable(f"{self.name} returned {getattr(response, 'status_code', 'no response')}")

        usage: dict = {}  # parse_stream_event records token counts and "complete" here
        started = time.monotonic()
        emitted = False
        try:
            # chunk_size=1: hand over each line as soon as it arrives (the default waits for 512 bytes)
            for raw in response.iter_lines(chunk_size=1):
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    usage["complete"] = True
                    break
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except ValueError:
                    continue
                text = self.parse_stream_event(data, usage)
                if text:
                    emitted = True
                    yield text
        except requests.RequestException as error:
            raise LLMUnavailable(f"{self.name} stream interrupted: {error}") from error
        finally:
            response.close()
        if not emitted:
            raise LLMUnavailable(f"{self.name} returned an empty stream.")
        if not usage.get("complete"):
            # The connection ended without the vendor's "finished" marker: the answer is cut off.
            message = f"{self.name} stream ended before the answer was complete."
            if self.strict_completion:
                raise LLMUnavailable(message)
            logger.warning(message)
        logger.info(
            "LLM stream ok provider={} model={} {}ms input_tokens={} output_tokens={}",
            self.name,
            self.model,
            round((time.monotonic() - started) * 1000),
            usage.get("input"),
            usage.get("output"),
        )


class GeminiProvider(BaseProvider):
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    @classmethod
    def from_settings(cls, s: Settings) -> "GeminiProvider":
        return cls(s, s.GEMINI_MODEL, s.GEMINI_API_KEY, s.GEMINI_MAX_RPM)

    def build_request(self, system, user, temperature):
        config: dict = {"maxOutputTokens": MAX_OUTPUT_TOKENS, "thinkingConfig": {"thinkingBudget": 0}}
        if temperature is not None:
            config["temperature"] = temperature
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        return self.URL.format(model=self.model), {"x-goog-api-key": self.api_key}, body

    def parse(self, data):
        parts = data["candidates"][0]["content"]["parts"]
        usage = data.get("usageMetadata", {})
        return Reply(
            "".join(p.get("text", "") for p in parts),
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
        )

    def retry_delay_s(self, response):
        return _retry_delay_s(response)

    def build_stream_request(self, system, user, temperature):
        url, headers, body = self.build_request(system, user, temperature)
        return url.replace(":generateContent", ":streamGenerateContent") + "?alt=sse", headers, body

    def parse_stream_event(self, data, usage):
        if "error" in data:
            raise ProviderFailure(f"gemini stream error: {str(data['error'])[:200]}")
        meta = data.get("usageMetadata") or {}
        if meta:
            usage["input"], usage["output"] = meta.get("promptTokenCount"), meta.get("candidatesTokenCount")
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        if candidates[0].get("finishReason"):
            usage["complete"] = True
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts) or None


class OpenAIProvider(BaseProvider):
    name = "openai"
    # Models that only accept their default sampling settings
    FIXED_SAMPLING_PREFIXES = ("o1", "o3", "o4", "gpt-5")

    def __init__(self, settings, model, api_key, base_url, max_rpm=0):
        super().__init__(settings, model, api_key, max_rpm)
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenAIProvider":
        return cls(s, s.OPENAI_MODEL, s.OPENAI_API_KEY, s.OPENAI_BASE_URL, s.OPENAI_MAX_RPM)

    def build_request(self, system, user, temperature):
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        # OpenAI itself renamed max_tokens; compatible servers still expect the old name.
        body["max_completion_tokens" if self.name == "openai" else "max_tokens"] = MAX_OUTPUT_TOKENS
        if temperature is not None and not self.model.lower().startswith(self.FIXED_SAMPLING_PREFIXES):
            body["temperature"] = temperature
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return f"{self.base_url}/chat/completions", headers, body

    def parse(self, data):
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):  # some servers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        usage = data.get("usage") or {}
        return Reply(content or "", usage.get("prompt_tokens"), usage.get("completion_tokens"))

    def retry_delay_s(self, response):
        if "insufficient_quota" in getattr(response, "text", ""):
            return COOLDOWN_MAX_S  # out of credit, not a rate limit: waiting a second won't help
        headers = getattr(response, "headers", {})
        return _duration_s(headers.get("retry-after")) or _duration_s(headers.get("x-ratelimit-reset-requests"))

    def build_stream_request(self, system, user, temperature):
        url, headers, body = self.build_request(system, user, temperature)
        body["stream"] = True
        if self.name == "openai":  # ask for the final usage chunk; other servers may reject the option
            body["stream_options"] = {"include_usage": True}
        return url, headers, body

    def parse_stream_event(self, data, usage):
        if "error" in data:
            raise ProviderFailure(f"{self.name} stream error: {str(data['error'])[:200]}")
        meta = data.get("usage")
        if meta:
            usage["input"], usage["output"] = meta.get("prompt_tokens"), meta.get("completion_tokens")
        choices = data.get("choices") or []
        if not choices:
            return None
        if choices[0].get("finish_reason"):
            usage["complete"] = True
        return (choices[0].get("delta") or {}).get("content") or None


class OpenAICompatibleProvider(OpenAIProvider):
    """Any server that speaks the OpenAI chat-completions protocol (Ollama, vLLM, OpenRouter, Groq...)."""

    name = "openai_compatible"
    strict_completion = False  # tiny local servers differ in how they end a stream; warn instead of failing

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenAICompatibleProvider":
        return cls(s, s.LLM_COMPAT_MODEL, s.LLM_COMPAT_API_KEY, s.LLM_COMPAT_BASE_URL or "", s.LLM_COMPAT_MAX_RPM)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)  # a local server needs no key


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    VERSION = "2023-06-01"

    def __init__(self, settings, model, api_key, base_url, max_rpm=0):
        super().__init__(settings, model, api_key, max_rpm)
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(cls, s: Settings) -> "AnthropicProvider":
        return cls(s, s.ANTHROPIC_MODEL, s.ANTHROPIC_API_KEY, s.ANTHROPIC_BASE_URL, s.ANTHROPIC_MAX_RPM)

    def build_request(self, system, user, temperature):
        body: dict = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"x-api-key": self.api_key, "anthropic-version": self.VERSION}
        return f"{self.base_url}/v1/messages", headers, body

    def parse(self, data):
        text = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
        usage = data.get("usage") or {}
        return Reply(text, usage.get("input_tokens"), usage.get("output_tokens"))

    def build_stream_request(self, system, user, temperature):
        url, headers, body = self.build_request(system, user, temperature)
        body["stream"] = True
        return url, headers, body

    def parse_stream_event(self, data, usage):
        kind = data.get("type")
        if kind == "error":
            raise LLMUnavailable(f"anthropic stream error: {str((data.get('error') or {}).get('message'))[:200]}")
        if kind == "message_start":
            usage["input"] = ((data.get("message") or {}).get("usage") or {}).get("input_tokens")
        elif kind == "message_delta":
            usage["output"] = (data.get("usage") or {}).get("output_tokens")
        elif kind == "message_stop":
            usage["complete"] = True
        elif kind == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                return delta.get("text") or None
        return None


PROVIDERS: dict[str, type[BaseProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "openai_compatible": OpenAICompatibleProvider,
}
ALIASES = {
    "google": "gemini",
    "claude": "anthropic",
    "compat": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "ollama": "openai_compatible",
    "openrouter": "openai_compatible",
    "groq": "openai_compatible",
}
