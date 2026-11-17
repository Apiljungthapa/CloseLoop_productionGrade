"""Gateway end to end in-process: real HTTP layer, planner, merger, validator; fake Qdrant and LLM."""
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.deps import Services, get_services
from core.config import Settings
from planner.query_planner import QueryPlanner
from retrieval.hybrid_retriever import HybridRetriever
from shared.schemas.retrieval_result import RetrievedItem


class FakeRag:
    def __init__(self, items):
        self.items = items

    def search(self, query, top_k=5, document_id=None, tenant_id=None):
        return self.items

    def is_healthy(self):
        return True

    def warm_up(self):
        pass


class FakeGenerator:
    def generate(self, question, evidence, extra_instruction=""):
        return f"Answer based on {len(evidence)} source(s)."


def client_with(items):
    settings = Settings(_env_file=None)
    services = Services(
        settings=settings,
        planner=QueryPlanner(db_enabled=False),
        retriever=HybridRetriever(settings, FakeRag(items)),
        generator=FakeGenerator(),
    )
    app = create_app()
    app.dependency_overrides[get_services] = lambda: services
    get_services.cache_clear()
    return TestClient(app, raise_server_exceptions=False)


HIT = RetrievedItem(text="refund within 30 days", score=0.9, semantic_score=0.9, document_id="a", title="Policy")


def test_ask_answers_from_evidence():
    body = client_with([HIT]).post("/ask", json={"question": "refund days?"}).json()
    assert body["route"] == "doc" and body["answer"] == "Answer based on 1 source(s)."
    assert body["sources"][0]["document_id"] == "a"


def test_ask_says_not_found_without_evidence():
    body = client_with([]).post("/ask", json={"question": "unknown topic"}).json()
    assert "couldn't find" in body["answer"] and body["sources"] == []


def test_chitchat_skips_retrieval():
    body = client_with([HIT]).post("/ask", json={"question": "hello"}).json()
    assert body["route"] == "chitchat" and body["sources"] == []


def test_validation_and_retrieve():
    c = client_with([HIT])
    assert c.post("/ask", json={"question": ""}).status_code == 422
    assert c.post("/retrieve", json={"query": "refund", "top_k": 3}).json()["total_matches"] == 1
    assert c.get("/health").json() == {"status": "ok"}
