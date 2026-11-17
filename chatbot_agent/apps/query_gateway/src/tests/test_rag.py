import pytest
from qdrant_client import QdrantClient, models

from core.config import Settings
from retrieval.rag_retriever import RagRetriever
from retrieval.reranker import rerank
from shared.schemas.retrieval_result import RetrievedItem


class FakeEmbedder:
    def encode(self, text, normalize_embeddings=True):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def retriever(tmp_path):
    settings = Settings(QDRANT_COLLECTION="t", QDRANT_VECTOR_SIZE=3, _env_file=None)
    client = QdrantClient(path=str(tmp_path))
    client.create_collection("t", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    client.upsert("t", points=[
        models.PointStruct(id=1, vector=[1, 0, 0], payload={"text": "refund within 30 days", "document_id": "a", "main_title": "A", "tenant_id": "shared"}),
        models.PointStruct(id=2, vector=[0, 1, 0], payload={"text": "shipping takes 5 days", "document_id": "b", "main_title": "B", "tenant_id": "shared"}),
        # uploaded by companies
        models.PointStruct(id=3, vector=[0.9, 0.1, 0], payload={"text": "acme travel budget", "document_id": "acme__plan.pdf", "main_title": "plan", "tenant_id": "acme"}),
        models.PointStruct(id=4, vector=[0.95, 0.05, 0], payload={"text": "globex travel budget", "document_id": "globex__plan.pdf", "main_title": "plan", "tenant_id": "globex"}),
        # an older chunk written before tenants existed: belongs to nobody, so nobody searches it
        models.PointStruct(id=5, vector=[1, 0, 0], payload={"text": "untagged legacy chunk", "document_id": "legacy"}),
    ])
    return RagRetriever(settings, client=client, embedder=FakeEmbedder())


def test_search_returns_best_first_with_metadata(retriever):
    items = retriever.search("refund", top_k=2)
    assert items[0].document_id == "a" and items[0].title == "A"


def test_search_without_a_tenant_sees_only_shared_documents(retriever):
    items = retriever.search("budget", top_k=10)
    assert {i.document_id for i in items} == {"a", "b"}


def test_search_sees_own_tenant_and_shared_but_never_another_tenant(retriever):
    acme = {i.document_id for i in retriever.search("budget", top_k=10, tenant_id="acme")}
    globex = {i.document_id for i in retriever.search("budget", top_k=10, tenant_id="globex")}
    assert acme == {"a", "b", "acme__plan.pdf"}
    assert globex == {"a", "b", "globex__plan.pdf"}


def test_untagged_legacy_chunks_are_not_searchable(retriever):
    assert "legacy" not in {i.document_id for i in retriever.search("legacy", top_k=10, tenant_id="acme")}


def test_document_filter_cannot_reach_another_tenants_document(retriever):
    assert retriever.search("budget", top_k=5, document_id="globex__plan.pdf", tenant_id="acme") == []


def test_search_filters_by_document(retriever):
    items = retriever.search("refund", top_k=5, document_id="b")
    assert {i.document_id for i in items} == {"b"}


def test_rerank_dedups_and_applies_threshold():
    items = [
        RetrievedItem(text="refund within 30 days", score=0.9),
        RetrievedItem(text="Refund  within 30 DAYS", score=0.9),  # duplicate
        RetrievedItem(text="weak match", score=0.05),
    ]
    out = rerank("refund days", items, min_score=0.3)
    assert len(out) == 1 and out[0].keyword_score > 0
