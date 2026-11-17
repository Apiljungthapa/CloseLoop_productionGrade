"""Backwards-compatible names. The implementation now lives in the ``llm`` package, which supports
Gemini, OpenAI, Anthropic (Claude) and OpenAI-compatible servers behind one client."""
import requests  # noqa: F401  (tests patch ``gemini_client.requests``)

from llm.client import LLMClient as GeminiClient  # noqa: F401
from llm.providers import _retry_delay_s  # noqa: F401
