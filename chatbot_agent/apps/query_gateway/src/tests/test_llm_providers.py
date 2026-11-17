import json

import pytest

from core.config import Settings
from core.exceptions import LLMUnavailable, RateLimited
from llm import providers
from llm.client import LLMClient, build_chain


def cfg(**overrides) -> Settings:
    """Settings that ignore the developer's real .env."""
    return Settings(_env_file=None, **overrides)


class Resp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "from gemini"}]}}], "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}
OPENAI_OK = {"choices": [{"message": {"content": "from openai"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 1}}
CLAUDE_OK = {"content": [{"type": "text", "text": "from claude"}], "usage": {"input_tokens": 5, "output_tokens": 2}}


class FakeNet:
    """Routes requests.post by URL to a scripted list of responses and records every call."""

    def __init__(self, monkeypatch, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls = []
        monkeypatch.setattr(providers.requests, "post", self.post)
        monkeypatch.setattr(providers.time, "sleep", lambda _s: None)

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "body": json})
        for key, queue in self.routes.items():
            if key in url:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"unexpected URL {url}")

    def hits(self, key):
        return [c for c in self.calls if key in c["url"]]


ALL = dict(
    GEMINI_API_KEY="g-key", GEMINI_MODEL="gem-model",
    OPENAI_API_KEY="o-key", OPENAI_MODEL="gpt-4.1-mini",
    ANTHROPIC_API_KEY="a-key", ANTHROPIC_MODEL="claude-sonnet-5-5",
)


# ---- request / response shapes -----------------------------------------------------------------
def test_gemini_request_and_parse(monkeypatch):
    net = FakeNet(monkeypatch, {"generativelanguage": [Resp(body=GEMINI_OK)]})
    client = LLMClient(cfg(LLM_PROVIDER="gemini", **ALL))

    assert client.generate("sys", "usr", temperature=0) == "from gemini"
    call = net.calls[0]
    assert call["url"].endswith("/models/gem-model:generateContent")
    assert call["headers"] == {"x-goog-api-key": "g-key"}
    assert call["body"]["system_instruction"]["parts"][0]["text"] == "sys"
    assert call["body"]["generationConfig"]["temperature"] == 0


def test_openai_request_and_parse(monkeypatch):
    net = FakeNet(monkeypatch, {"api.openai.com": [Resp(body=OPENAI_OK)]})
    client = LLMClient(cfg(LLM_PROVIDER="openai", **ALL))

    assert client.generate("sys", "usr", temperature=0.2) == "from openai"
    call = net.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer o-key"}
    assert call["body"]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert call["body"]["temperature"] == 0.2 and "max_completion_tokens" in call["body"]


@pytest.mark.parametrize("model", ["gpt-5-mini", "o3", "o4-mini"])
def test_openai_reasoning_models_get_no_temperature(monkeypatch, model):
    net = FakeNet(monkeypatch, {"api.openai.com": [Resp(body=OPENAI_OK)]})
    LLMClient(cfg(LLM_PROVIDER="openai", **{**ALL, "OPENAI_MODEL": model})).generate("s", "u", temperature=0)

    assert "temperature" not in net.calls[0]["body"]


def test_anthropic_request_and_parse(monkeypatch):
    net = FakeNet(monkeypatch, {"api.anthropic.com": [Resp(body=CLAUDE_OK)]})
    client = LLMClient(cfg(LLM_PROVIDER="claude", **ALL))  # "claude" is an alias for anthropic

    assert client.generate("sys", "usr", temperature=0) == "from claude"
    call = net.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "a-key" and call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["body"]["system"] == "sys" and call["body"]["messages"] == [{"role": "user", "content": "usr"}]
    assert call["body"]["model"] == "claude-sonnet-5-5" and call["body"]["max_tokens"] > 0


def test_openai_compatible_local_server_needs_no_key(monkeypatch):
    net = FakeNet(monkeypatch, {"11434": [Resp(body=OPENAI_OK)]})
    client = LLMClient(
        cfg(LLM_PROVIDER="ollama", LLM_COMPAT_BASE_URL="http://host.docker.internal:11434/v1", LLM_COMPAT_MODEL="llama3.1")
    )

    assert client.generate("s", "u", temperature=0) == "from openai"
    call = net.calls[0]
    assert call["url"] == "http://host.docker.internal:11434/v1/chat/completions"
    assert call["headers"] == {} and "max_tokens" in call["body"] and "max_completion_tokens" not in call["body"]


def test_temperature_rejected_by_a_model_is_retried_without_it(monkeypatch):
    net = FakeNet(
        monkeypatch,
        {"api.anthropic.com": [Resp(400, text='{"error":"temperature is not supported for this model"}'), Resp(body=CLAUDE_OK)]},
    )

    assert LLMClient(cfg(LLM_PROVIDER="anthropic", **ALL)).generate("s", "u", temperature=0) == "from claude"
    assert "temperature" in net.calls[0]["body"] and "temperature" not in net.calls[1]["body"]


# ---- which providers are active ----------------------------------------------------------------------
def test_primary_and_fallback_order_follow_the_env():
    chain, warnings = build_chain(cfg(LLM_PROVIDER="anthropic", LLM_FALLBACKS="openai, gemini", **ALL))

    assert [p.name for p in chain] == ["anthropic", "openai", "gemini"] and warnings == []
    assert LLMClient(cfg(LLM_PROVIDER="anthropic", LLM_FALLBACKS="openai", **ALL)).model_id == "anthropic/claude-sonnet-5-5"


def test_unconfigured_unknown_and_duplicate_providers_are_skipped_with_warnings():
    chain, warnings = build_chain(
        cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai,gemini,nonsense,anthropic", GEMINI_API_KEY="k")
    )

    assert [p.name for p in chain] == ["gemini"]
    text = " ".join(warnings)
    assert "openai" in text and "nonsense" in text and "anthropic" in text and len(warnings) == 3


def test_openai_needs_a_model_and_compat_needs_a_base_url():
    chain, warnings = build_chain(cfg(LLM_PROVIDER="openai", LLM_FALLBACKS="openai_compatible", OPENAI_API_KEY="k"))

    assert chain == [] and len(warnings) == 2


def test_no_provider_configured_fails_clearly():
    with pytest.raises(LLMUnavailable, match="No LLM provider"):
        LLMClient(cfg()).generate("s", "u")


# ---- failover ------------------------------------------------------------------------------------------
QUOTA = Resp(429, text='{"error":{"message":"Please retry in 39s."}, "retryDelay": "39s"}')


def test_rate_limited_primary_falls_over_to_the_next_provider(monkeypatch):
    net = FakeNet(monkeypatch, {"generativelanguage": [QUOTA], "api.openai.com": [Resp(body=OPENAI_OK)]})
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    assert client.generate("s", "u") == "from openai"
    assert len(net.hits("generativelanguage")) == 1  # 429 with a long delay is not retried
    assert client.describe()[0]["state"] == "cooling_down"

    assert client.generate("s", "u") == "from openai"  # the cooling provider is skipped entirely
    assert len(net.hits("generativelanguage")) == 1


def test_rejected_key_fails_over_and_the_bad_provider_is_parked(monkeypatch):
    net = FakeNet(
        monkeypatch,
        {"generativelanguage": [Resp(403, text='{"error":"Your project has been denied access."}')],
         "api.anthropic.com": [Resp(body=CLAUDE_OK)]},
    )
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="anthropic", **ALL))

    assert client.generate("s", "u") == "from claude"
    client.generate("s", "u")
    assert len(net.hits("generativelanguage")) == 1 and len(net.hits("api.anthropic.com")) == 2


def test_server_errors_are_retried_then_fail_over_with_a_short_breaker(monkeypatch):
    net = FakeNet(
        monkeypatch,
        {"generativelanguage": [Resp(503, text="overloaded")], "api.openai.com": [Resp(body=OPENAI_OK)]},
    )
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    assert client.generate("s", "u") == "from openai"
    assert len(net.hits("generativelanguage")) == 3  # three attempts before giving up on it
    client.generate("s", "u")
    assert len(net.hits("generativelanguage")) == 3  # breaker: no new attempts for a while


def test_a_bad_request_fails_over_but_does_not_park_the_provider(monkeypatch):
    net = FakeNet(
        monkeypatch,
        {"generativelanguage": [Resp(400, text='{"error":"prompt too long"}')], "api.openai.com": [Resp(body=OPENAI_OK)]},
    )
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    client.generate("s", "u")
    client.generate("s", "u")
    assert len(net.hits("generativelanguage")) == 2  # tried again: a bad request is not the provider's fault
    assert client.describe()[0]["state"] == "ready"


def test_all_providers_rate_limited_returns_429_with_the_soonest_retry(monkeypatch):
    FakeNet(
        monkeypatch,
        {"generativelanguage": [QUOTA],
         "api.openai.com": [Resp(429, text='{"error":{"code":"insufficient_quota"}}')]},
    )
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    with pytest.raises(RateLimited) as error:
        client.generate("s", "u")

    assert error.value.status_code == 429 and error.value.retry_after == 39


def test_mixed_failures_raise_a_502_style_error_naming_each_provider(monkeypatch):
    FakeNet(
        monkeypatch,
        {"generativelanguage": [QUOTA], "api.openai.com": [Resp(401, text="bad key")]},
    )
    client = LLMClient(cfg(LLM_PROVIDER="gemini", LLM_FALLBACKS="openai", **ALL))

    with pytest.raises(LLMUnavailable) as error:
        client.generate("s", "u")

    assert not isinstance(error.value, RateLimited)
    assert "gemini" in str(error.value) and "openai" in str(error.value)


def test_openai_insufficient_quota_is_treated_as_a_long_cooldown(monkeypatch):
    FakeNet(monkeypatch, {"api.openai.com": [Resp(429, text='{"error":{"code":"insufficient_quota"}}')]})
    client = LLMClient(cfg(LLM_PROVIDER="openai", **ALL))

    with pytest.raises(RateLimited) as error:
        client.generate("s", "u")

    assert error.value.retry_after == providers.COOLDOWN_MAX_S


def test_retry_after_header_is_honoured(monkeypatch):
    FakeNet(monkeypatch, {"api.anthropic.com": [Resp(429, text="slow down", headers={"retry-after": "42"})]})

    with pytest.raises(RateLimited) as error:
        LLMClient(cfg(LLM_PROVIDER="anthropic", **ALL)).generate("s", "u")

    assert error.value.retry_after == 42


def test_duration_parsing():
    assert providers._duration_s("30") == 30
    assert providers._duration_s("6m0s") == 360
    assert providers._duration_s("250ms") == 1
    assert providers._duration_s(None) == 0
