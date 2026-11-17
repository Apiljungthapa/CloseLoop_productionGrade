from vector_store.qdrant_store import QdrantVectorStore


def make_store(tmp_path):
    return QdrantVectorStore(
        collection_name="t", vector_size=3, url=None, api_key=None, local_path=str(tmp_path)
    )


def chunk(doc, i, text, vec):
    return {"text": text, "document_id": doc, "chunk_id": i, "main_title": doc, "embedding": vec}


def test_upsert_list_get_delete(tmp_path, monkeypatch):
    monkeypatch.delenv("QDRANT_CLUSTER_ENDPOINT", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    store = make_store(tmp_path)
    store.upsert_chunks([
        chunk("a", 1, "refund within 30 days", [1.0, 0.0, 0.0]),
        chunk("a", 2, "second chunk", [0.0, 1.0, 0.0]),
        chunk("b", 1, "unrelated shipping text", [0.0, 0.0, 1.0]),
    ])

    assert {d["document_id"]: d["chunk_count"] for d in store.list_documents()} == {"a": 2, "b": 1}
    assert [c["chunk_id"] for c in store.get_document_chunks("a")] == [1, 2]

    store.delete_document("a")
    assert store.get_document_chunks("a") == []
    store.close()


def test_tenant_documents_are_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("QDRANT_CLUSTER_ENDPOINT", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    store = make_store(tmp_path)
    store.upsert_chunks([
        {**chunk("acme__plan.pdf", 1, "acme budget", [1.0, 0.0, 0.0]), "tenant_id": "acme", "uploaded_by": "u1"},
        {**chunk("globex__plan.pdf", 1, "globex budget", [0.0, 1.0, 0.0]), "tenant_id": "globex"},
        chunk("manual", 1, "product reference", [0.0, 0.0, 1.0]),  # no tenant -> shared
    ])

    assert [d["document_id"] for d in store.list_documents("acme")] == ["acme__plan.pdf"]
    assert store.list_documents("acme")[0]["uploaded_by"] == "u1"
    assert [d["document_id"] for d in store.list_documents("globex")] == ["globex__plan.pdf"]
    assert {d["document_id"] for d in store.list_documents()} == {"acme__plan.pdf", "globex__plan.pdf", "manual"}

    # Another tenant can neither read nor delete a document, even knowing its id.
    assert store.get_document_chunks("acme__plan.pdf", "globex") == []
    store.delete_document("acme__plan.pdf", "globex")
    assert len(store.get_document_chunks("acme__plan.pdf", "acme")) == 1

    store.delete_document("acme__plan.pdf", "acme")
    assert store.get_document_chunks("acme__plan.pdf", "acme") == []
    store.close()


def test_chunks_default_to_shared_and_text_cannot_choose_its_tenant(tmp_path, monkeypatch):
    monkeypatch.delenv("QDRANT_CLUSTER_ENDPOINT", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    store = make_store(tmp_path)
    store.upsert_chunks([
        chunk("manual", 1, "reference", [1.0, 0.0, 0.0]),
        # metadata comes from the uploaded file's own content: it must not override the tenant
        {**chunk("acme__x.xml", 1, "x", [0.0, 1.0, 0.0]), "tenant_id": "acme", "metadata": {"tenant_id": "globex"}},
    ])
    assert store.get_document_chunks("manual")[0]["tenant_id"] == "shared"
    assert store.get_document_chunks("acme__x.xml")[0]["tenant_id"] == "acme"
    store.close()
