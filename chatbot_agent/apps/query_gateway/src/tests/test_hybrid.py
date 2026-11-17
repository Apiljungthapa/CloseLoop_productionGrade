from core.config import Settings
from domain.models import QueryPlan
from evidence.merger import merge
from evidence.validator import has_sufficient_evidence
from retrieval.hybrid_retriever import HybridRetriever
from shared.constants.enums import EvidenceSource, Route
from shared.schemas.retrieval_result import RetrievedItem


class FakeRag:
    def search(self, query, top_k=5, document_id=None, tenant_id=None):
        return [RetrievedItem(text="refund within 30 days", score=0.9, semantic_score=0.9, document_id="a")]


class FakeDb:
    def retrieve(self, question):
        return [RetrievedItem(text="count=3", score=1.0, source=EvidenceSource.DATABASE)]


def make(db=None):
    return HybridRetriever(Settings(_env_file=None), FakeRag(), db or FakeDb())


def test_doc_route_only_hits_documents():
    out = make().retrieve("refund", QueryPlan(route=Route.DOC))
    assert [i.source for i in out] == [EvidenceSource.DOCUMENT]


def test_hybrid_route_merges_both_sources():
    out = merge(make().retrieve("refund", QueryPlan(route=Route.HYBRID)))
    assert {i.source for i in out} == {EvidenceSource.DOCUMENT, EvidenceSource.DATABASE}
    assert out[0].source == EvidenceSource.DATABASE  # score 1.0 first


def test_db_not_implemented_is_skipped_not_fatal():
    from retrieval.db_retriever import DbRetriever

    out = make(DbRetriever()).retrieve("how many", QueryPlan(route=Route.HYBRID))
    assert [i.source for i in out] == [EvidenceSource.DOCUMENT]


def test_chitchat_retrieves_nothing_and_empty_is_insufficient():
    assert make().retrieve("hi", QueryPlan(route=Route.CHITCHAT)) == []
    assert not has_sufficient_evidence([])
