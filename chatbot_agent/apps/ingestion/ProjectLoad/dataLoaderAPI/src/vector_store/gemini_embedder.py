"""Gemini embeddings over REST (replaces sentence-transformers)."""
import hashlib
import math
import os

import requests

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
_BATCH = 100


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dims: int = 512, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")
        self.api_key, self.model, self.dims, self.timeout = api_key, model, dims, timeout

    def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if os.environ.get("INGEST_FAKE_EMBEDDINGS") == "true":  # load-testing the queue only: no Gemini calls
            return [self._fake(t) for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            body = {"requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dims,
                }
                for t in texts[i:i + _BATCH]
            ]}
            r = requests.post(
                _URL.format(model=self.model), json=body, timeout=self.timeout,
                headers={"x-goog-api-key": self.api_key},
            )
            r.raise_for_status()
            for e in r.json()["embeddings"]:
                v = [float(x) for x in e["values"]]
                n = math.sqrt(sum(x * x for x in v)) or 1.0  # truncated dims aren't unit length
                out.append([x / n for x in v])
        return out

    def _fake(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        v = [seed[i % 32] - 127.5 for i in range(self.dims)]
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts, "RETRIEVAL_DOCUMENT")
