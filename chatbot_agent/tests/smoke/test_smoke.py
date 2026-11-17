"""Run against live services: SMOKE_INGESTION_URL / SMOKE_GATEWAY_URL (defaults to localhost)."""
import os

import pytest
import requests

INGESTION = os.getenv("SMOKE_INGESTION_URL", "http://127.0.0.1:8001")
GATEWAY = os.getenv("SMOKE_GATEWAY_URL", "http://127.0.0.1:8010")


def up(url):
    try:
        return requests.get(f"{url}/health", timeout=3).ok
    except requests.RequestException:
        return False


@pytest.mark.parametrize("name,url", [("ingestion", INGESTION), ("gateway", GATEWAY)])
def test_service_health_and_ready(name, url):
    if not up(url):
        pytest.skip(f"{name} not running at {url}")
    assert requests.get(f"{url}/ready", timeout=10).status_code == 200
