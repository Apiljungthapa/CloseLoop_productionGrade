from loguru import logger

from core import cache as cache_layer
from core.cache import DOCS_VERSION_KEY, normalize_text
from core.config import Settings
from core.exceptions import FeatureDisabled
from domain.models import QueryPlan
from shared.constants.enums import Route
from shared.schemas.retrieval_result import RetrievedItem

from .db_retriever import DbRetriever
from .rag_retriever import RagRetriever
# from .reranker import rerank  # disabled: plain vector search for now


class HybridRetriever:
    """Executes a QueryPlan by calling the document and/or database retrievers."""

    def __init__(self, settings: Settings, rag: RagRetriever, db: DbRetriever | None = None) -> None:
        self.settings = settings
        self.rag = rag
        self.db = db or DbRetriever()

    def docs(
        self,
        query: str,
        top_k: int,
        document_id: str | None,
        min_score: float | None = None,
        tenant_id: str | None = None,
    ) -> list[RetrievedItem]:
        """Plain vector search: Qdrant similarity order, no reranking. Cached per document-set version
        and per tenant (a company's uploads are searched together with the shared documents only)."""
        threshold = self.settings.MIN_RETRIEVAL_SCORE if min_score is None else min_score
        cache = cache_layer.get_cache()
        # The ingestion service bumps this counter on every upload/delete, which retires every entry.
        key = cache.make_key(
            "docs",
            normalize_text(query),
            top_k,
            document_id,
            threshold,
            self.settings.QDRANT_COLLECTION,
            cache.get_int(DOCS_VERSION_KEY),
            tenant=tenant_id,
        )
        cached = cache.get_json("docs", key)
        if isinstance(cached, list):
            return [RetrievedItem(**item) for item in cached]

        hits = self.rag.search(query, top_k=top_k, document_id=document_id, tenant_id=tenant_id)
        result = [h for h in hits if h.score >= threshold][:top_k]
        if result:  # never pin an empty answer
            cache.set_json("docs", key, [item.model_dump(mode="json") for item in result], self.settings.CACHE_TTL_DOCS_S)
        return result

        # --- disabled for now: keyword/semantic rerank ---
        # raw = self.rag.search(query, top_k=top_k, document_id=document_id)
        # return rerank(
        #     query,
        #     raw,
        #     semantic_weight=self.settings.SEMANTIC_WEIGHT,
        #     min_score=threshold,
        #     top_k=top_k,
        # )

    # --- disabled for now: plan/intent-based routing (doc / db / hybrid) ---
    # def retrieve(self, query: str, plan: QueryPlan) -> list[RetrievedItem]:
    #     if plan.route == Route.CHITCHAT:
    #         return []
    #     evidence: list[RetrievedItem] = []
    #     if plan.route in (Route.DOC, Route.HYBRID):
    #         evidence += self.docs(query, plan.top_k, plan.document_id)
    #     if plan.route in (Route.DB, Route.HYBRID):
    #         try:
    #             evidence += self.db.retrieve(query)
    #         except FeatureDisabled as error:
    #             logger.warning(f"DB route skipped: {error}")
    #     return evidence
