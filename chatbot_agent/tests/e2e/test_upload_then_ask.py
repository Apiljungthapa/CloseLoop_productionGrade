"""Upload a document through ingestion, then ask the gateway about it. Needs both services and Gemini."""
import io
import os
import uuid

import pytest
import requests

INGESTION = os.getenv("SMOKE_INGESTION_URL", "http://127.0.0.1:8001")
GATEWAY = os.getenv("SMOKE_GATEWAY_URL", "http://127.0.0.1:8010")


def up(url):
    try:
        return requests.get(f"{url}/health", timeout=3).ok
    except requests.RequestException:
        return False


@pytest.mark.skipif(not (up(INGESTION) and up(GATEWAY)), reason="services not running")
def test_upload_then_ask():
    name = f"e2e-{uuid.uuid4().hex[:6]}"
    html = b"<html><body><h1>Zorblax Policy</h1><p>Zorblax devices carry a 17 day warranty.</p></body></html>"
    up_resp = requests.post(f"{INGESTION}/upload", files={"file": (f"{name}.html", io.BytesIO(html), "text/html")}, timeout=120)
    assert up_resp.status_code == 200
    try:
        ask = requests.post(f"{GATEWAY}/ask", json={"question": "How many days is the Zorblax warranty?", "document_id": name}, timeout=60)
        assert ask.status_code == 200 and "17" in ask.json()["answer"]
    finally:
        requests.delete(f"{INGESTION}/documents/{name}", timeout=30)
