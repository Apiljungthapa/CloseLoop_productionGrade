import os
import uuid
from typing import Any, Iterable

from loguru import logger
from qdrant_client import QdrantClient, models

# Chunks that belong to no company (the product reference docs loaded by the CLI) carry this tenant.
# Must match SHARED_TENANT in apps/query_gateway/src/retrieval/rag_retriever.py
SHARED_TENANT = "shared"


class QdrantVectorStore:
    """Write side of Qdrant: persist chunks and manage documents. Search lives in the query gateway."""

    def __init__(
        self,
        collection_name: str = "rag_documents",
        vector_size: int = 512,
        url: str | None = None,
        api_key: str | None = None,
        local_path: str = "./qdrant_storage",
    ) -> None:
        self.collection_name = collection_name
        self.vector_size = int(vector_size)
        self.url = self._resolve_value(url, "QDRANT_CLUSTER_ENDPOINT")
        self.api_key = self._resolve_value(api_key, "QDRANT_API_KEY")
        self.local_path = self._resolve_value(local_path, "QDRANT_LOCAL_PATH") or "./qdrant_storage"

        if self.url and self.api_key:
            self.client = QdrantClient(url=self.url, api_key=self.api_key, prefer_grpc=False)
        else:
            self.client = self._create_local_client(self.local_path)

        self._ensure_collection()

    @staticmethod
    def _resolve_value(value: str | None, env_name: str) -> str | None:
        if value is None:
            return os.getenv(env_name)
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            return os.getenv(env_key)
        return value

    @staticmethod
    def _create_local_client(path: str) -> QdrantClient:
        try:
            return QdrantClient(path=path)
        except RuntimeError as exc:
            if "already accessed by another instance" not in str(exc):
                raise
            fallback = os.path.join(os.getcwd(), f"qdrant_storage_{uuid.uuid4().hex[:8]}")
            logger.warning(
                f"Qdrant local storage '{path}' is locked; retrying with fallback path '{fallback}'"
            ) 
            return QdrantClient(path=fallback)

    def close(self) -> None:
        try:
            client = getattr(self, "client", None)
            if client is not None:
                client.close()
                self.client = None
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info(f"Created Qdrant collection '{self.collection_name}' with size {self.vector_size}")

        # Qdrant Cloud requires a payload index before filtering on a field
        for field in ("document_id", "tenant_id"):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception as error:
                logger.warning(f"Could not create '{field}' payload index: {error}")

    @staticmethod
    def _normalize_vector(vector: Iterable[float], vector_size: int) -> list[float]:
        values = [float(value) for value in vector]
        if len(values) < vector_size:
            values = values + [0.0] * (vector_size - len(values))
        elif len(values) > vector_size:
            values = values[:vector_size]
        return values

    def upsert_chunks(self, chunks: list[dict]) -> list[dict]:
        valid_chunks = [chunk for chunk in chunks if str(chunk.get("text", "")).strip()]
        if not valid_chunks:
            return chunks

        points = []
        for chunk in valid_chunks:
            embedding = chunk.get("embedding")
            if not embedding:
                continue

            normalized = self._normalize_vector(embedding, self.vector_size)
            metadata = chunk.get("metadata") or {}
            payload = {
                "text": chunk.get("text", ""),
                "document_id": chunk.get("document_id", ""),
                "record_id": chunk.get("record_id"),
                "chunk_id": chunk.get("chunk_id"),
                "main_title": chunk.get("main_title", ""),
                "section_name": chunk.get("section_name", ""),
                "source": "data_loader",
                **metadata,
                # After **metadata on purpose: text extracted from an uploaded file must not be able
                # to name its own tenant.
                "tenant_id": chunk.get("tenant_id") or SHARED_TENANT,
                "uploaded_by": chunk.get("uploaded_by") or "",
            }
            document_id = str(chunk.get("document_id") or chunk.get("record_id") or "document")
            chunk_index = chunk.get("chunk_id", len(points) + 1)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}-{chunk_index}"))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=normalized,
                    payload=payload,
                )
            )

        if points:
            self.client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Upserted {len(points)} chunk embeddings into Qdrant collection '{self.collection_name}'")

        return chunks

    @staticmethod
    def _build_filter(filter_metadata: dict[str, Any] | None) -> models.Filter | None:
        conditions = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in (filter_metadata or {}).items()
            if value is not None
        ]
        return models.Filter(must=conditions) if conditions else None

    # ---- data getter -------------------------------------------------
    def _scroll_all(
        self, scroll_filter: models.Filter | None = None, max_points: int = 100_000, fields: list[str] | None = None
    ):
        offset = None
        fetched = 0
        while fetched < max_points:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=scroll_filter,
                limit=256,
                offset=offset,
                with_payload=fields or True,
                with_vectors=False,
            )
            for point in points:
                fetched += 1
                yield point
            if offset is None:
                break

    def list_documents(self, tenant_id: str | None = None) -> list[dict]:
        """One row per document with chunk count. ``tenant_id`` limits it to one company's documents."""
        docs: dict[str, dict] = {}
        fields = ["document_id", "main_title", "title", "fileFormat", "uploaded_by"]
        for point in self._scroll_all(self._build_filter({"tenant_id": tenant_id}), fields=fields):
            payload = point.payload or {}
            doc_id = str(payload.get("document_id") or "unknown")
            entry = docs.setdefault(
                doc_id,
                {
                    "document_id": doc_id,
                    "title": payload.get("main_title") or payload.get("title"),
                    "file_format": payload.get("fileFormat"),
                    "uploaded_by": payload.get("uploaded_by") or None,
                    "chunk_count": 0,
                },
            )
            entry["chunk_count"] += 1
        return sorted(docs.values(), key=lambda d: d["document_id"])

    def get_document_chunks(self, document_id: str, tenant_id: str | None = None) -> list[dict]:
        flt = self._build_filter({"document_id": document_id, "tenant_id": tenant_id})
        chunks = [
            {"id": str(p.id), **(p.payload or {})} for p in self._scroll_all(flt)
        ]
        return sorted(chunks, key=lambda c: c.get("chunk_id") or 0)

    def delete_document(self, document_id: str, tenant_id: str | None = None) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=self._build_filter({"document_id": document_id, "tenant_id": tenant_id})
            ),
        )

    def is_healthy(self) -> bool:
        try:
            return bool(self.client.collection_exists(self.collection_name))
        except Exception:
            return False
