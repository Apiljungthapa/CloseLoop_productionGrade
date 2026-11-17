import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC.parents[2]
for path in (SRC, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


import pytest

from core import cache as cache_layer


@pytest.fixture(autouse=True)
def cache_off_by_default(monkeypatch):
    """Unit tests never touch a real Redis; tests that need a cache install their own."""
    monkeypatch.setattr(cache_layer, "get_cache", lambda: cache_layer.Cache(None))
