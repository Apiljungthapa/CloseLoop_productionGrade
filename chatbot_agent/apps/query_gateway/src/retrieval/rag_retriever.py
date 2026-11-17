"""Document retrieval: embed the query, search Qdrant, return raw semantic hits."""
from typing import Any

from loguru import logger
from qdrant_client import QdrantClient, models

from core.config import Settings
from core.exceptions import RetrievalUnavailable
from shared.constants.enums import EvidenceSource
from shared.schemas.retrieval_result import RetrievedItem
from shared.utils.llm_utils import normalize_vector

# Chunks that belong to no company (product reference docs) carry this tenant; every tenant may read them.
# Must match SHARED_TENANT in apps/ingestion/ProjectLoad/dataLoaderAPI/src/vector_store/qdrant_store.py
SHARED_TENANT = "shared"


class RagRetriever:
    def __init__(self, settings: Settings, client: QdrantClient | None = None, embedder: Any = None) -> None:
        self.settings = settings
        self._client = client
        self._embedder = embedder
        self._tenant_index_checked = False

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            s = self.settings
            if s.QDRANT_CLUSTER_ENDPOINT and s.QDRANT_API_KEY:
                self._client = QdrantClient(url=s.QDRANT_CLUSTER_ENDPOINT, api_key=s.QDRANT_API_KEY, prefer_grpc=False)
            else:
                self._client = QdrantClient(path=s.QDRANT_LOCAL_PATH)
        return self._client

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            from retrieval.gemini_embedder import GeminiEmbedder

            self._embedder = GeminiEmbedder(
                self.settings.GEMINI_API_KEY, self.settings.EMBEDDING_MODEL, self.settings.QDRANT_VECTOR_SIZE
            )
        return self._embedder

    def warm_up(self) -> None:
        _ = self.embedder  # validate the key/config at startup

    def is_healthy(self) -> bool:
        try:
            return bool(self.client.collection_exists(self.settings.QDRANT_COLLECTION))
        except Exception:
            return False

    def _ensure_tenant_index(self) -> None:
        """Qdrant Cloud refuses to filter on a field without a payload index. The ingestion service
        creates it too; doing it here as well (once, idempotent) means search works whichever starts first."""
        if self._tenant_index_checked:
            return
        self._tenant_index_checked = True
        try:
            self.client.create_payload_index(
                collection_name=self.settings.QDRANT_COLLECTION,
                field_name="tenant_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception as error:
            logger.warning("Could not create the 'tenant_id' payload index: {}", error)

    def search(
        self, query: str, top_k: int = 5, document_id: str | None = None, tenant_id: str | None = None
    ) -> list[RetrievedItem]:
        """Nearest chunks among the shared documents and, when ``tenant_id`` is given, that tenant's own.
        Never another tenant's: with no tenant only the shared documents are searched."""
        vector = normalize_vector(
            self.embedder.encode(query, normalize_embeddings=True), self.settings.QDRANT_VECTOR_SIZE
        )
        allowed = [SHARED_TENANT] if not tenant_id else [tenant_id, SHARED_TENANT]
        must = [models.FieldCondition(key="tenant_id", match=models.MatchAny(any=allowed))]
        if document_id:
            must.append(models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)))
        flt = models.Filter(must=must)
        self._ensure_tenant_index()
        try:
            response = self.client.query_points(
                collection_name=self.settings.QDRANT_COLLECTION,
                query=vector,
                limit=top_k,  # was max(top_k * 4, top_k) to feed the reranker (disabled)
                with_payload=True,
                with_vectors=False,
                query_filter=flt,
            )
        except Exception as error:
            raise RetrievalUnavailable(str(error)) from error

        items = []
        for point in response.points:
            p = point.payload or {}
            items.append(RetrievedItem(
                source=EvidenceSource.DOCUMENT,
                text=str(p.get("text", "") or ""),
                score=float(point.score or 0.0),
                semantic_score=float(point.score or 0.0),
                document_id=p.get("document_id"),
                title=p.get("main_title") or p.get("title"),
                section=p.get("section_name") or None,
                chunk_id=p.get("chunk_id"),
            ))
        return items
