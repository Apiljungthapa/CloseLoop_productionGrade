"""Gemini embeddings over REST (replaces sentence-transformers)."""
import math

import requests

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
_BATCH = 100


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dims: int = 512, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")
        self.api_key, self.model, self.dims, self.timeout = api_key, model, dims, timeout

    def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
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

    def encode(self, query: str, normalize_embeddings: bool = True) -> list[float]:
        return self.embed([query], "RETRIEVAL_QUERY")[0]
