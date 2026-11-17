"""Ingestion API is tenant-scoped: the tenant comes in a header (set by the CloseLoop backend), every
upload / job / document call is limited to it, and the worker refuses a job that has no tenant.
Redis, the queue and the embedder are faked, so this runs without Docker."""
import os
import tempfile

os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.pop("QDRANT_CLUSTER_ENDPOINT", None)
os.environ.pop("QDRANT_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

# `api` builds its vector store on import, in ./qdrant_storage (dev_config.yaml). Import it from a scratch
# directory so a test run never writes into the repo.
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="ingest-test-qdrant-"))
try:
    import api
    import jobs
    import tasks
finally:
    os.chdir(_cwd)

ACME = {"X-Tenant-Id": "acme", "X-User-Id": "u-acme"}
GLOBEX = {"X-Tenant-Id": "globex"}


class FakeRedis:
    """Just enough of redis-py for jobs.py. A pipeline applies writes at once and queues reads."""

    def __init__(self):
        self.hashes, self.sets = {}, {}
        self._reads = []

    def pipeline(self):
        self._reads = []
        return self

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})
        return self

    def expire(self, *_):
        return self

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)
        return self

    def hgetall(self, key):
        data = dict(self.hashes.get(key, {}))
        self._reads.append(data)
        return data

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def execute(self):
        reads, self._reads = self._reads, []
        return reads


@pytest.fixture
def env(monkeypatch, tmp_path):
    fake = FakeRedis()
    monkeypatch.setattr(jobs, "_r", lambda: fake)
    queued = []
    monkeypatch.setattr(api.process_document, "delay", lambda job_id, path: queued.append((job_id, path)))
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api, "bump_docs_version", lambda: None)

    # Real (local) store, fake embeddings.
    store = api.orchestrator.qdrant_store
    for tenant in ("acme", "globex"):
        for doc in store.list_documents(tenant):
            store.delete_document(doc["document_id"], tenant)

    def embed(chunks):
        for c in chunks:
            c["embedding"] = [1.0] + [0.0] * 511
        return chunks

    monkeypatch.setattr(api.orchestrator, "add_embeddings", embed)
    return TestClient(api.app), fake, queued


def seed(tenant, name, text="hello world"):
    path = os.path.join(tempfile.mkdtemp(prefix="ingest-test-file-"), name)
    with open(path, "w") as f:
        f.write(text)
    return api.orchestrator.run_local_file(path, strict=True, tenant_id=tenant, uploaded_by="u")


# ---- the tenant header ---------------------------------------------------------------------
@pytest.mark.parametrize("headers", [{}, {"X-Tenant-Id": ""}, {"X-Tenant-Id": "shared"}, {"X-Tenant-Id": "a b/c"}])
def test_every_data_endpoint_needs_a_valid_tenant(env, headers):
    client, _, queued = env
    assert client.get("/documents", headers=headers).status_code == 400
    assert client.get("/jobs/abc", headers=headers).status_code == 400
    assert client.get("/batches/abc", headers=headers).status_code == 400
    assert client.delete("/documents/x", headers=headers).status_code == 400
    r = client.post("/upload/batch", files=[("files", ("a.txt", b"hi", "text/plain"))], headers=headers)
    assert r.status_code == 400
    assert queued == []


def test_health_needs_no_tenant(env):
    assert env[0].get("/health").status_code == 200


def test_probes_need_no_key_but_everything_else_does(env, monkeypatch):
    monkeypatch.setattr(api.batch_config, "API_AUTH_KEY", "secret")
    client = env[0]
    assert client.get("/health").status_code == 200  # the container health check sends no key
    assert client.get("/documents", headers=ACME).status_code == 401
    assert client.get("/documents", headers={**ACME, "X-API-Key": "wrong"}).status_code == 401
    assert client.get("/documents", headers={**ACME, "X-API-Key": "secret"}).status_code == 200


# ---- uploads and jobs ----------------------------------------------------------------------
def test_upload_records_the_tenant_and_other_tenants_cannot_see_the_job(env):
    client, fake, queued = env
    r = client.post("/upload/batch", files=[("files", ("a.txt", b"hello", "text/plain"))], headers=ACME)
    assert r.status_code == 202 and r.json()["accepted"] == 1
    job_id = r.json()["jobs"][0]["job_id"]
    batch_id = r.json()["batch_id"]

    job = fake.hashes[f"ing:job:{job_id}"]
    assert job["tenant_id"] == "acme" and job["uploaded_by"] == "u-acme"
    assert queued and queued[0][0] == job_id

    assert client.get(f"/jobs/{job_id}", headers=ACME).status_code == 200
    assert client.get(f"/jobs/{job_id}", headers=GLOBEX).status_code == 404
    assert client.get(f"/batches/{batch_id}", headers=ACME).json()["total"] == 1
    assert client.get(f"/batches/{batch_id}", headers=GLOBEX).status_code == 404


def test_unsupported_extension_is_rejected_per_file(env):
    client, _, queued = env
    r = client.post(
        "/upload/batch",
        files=[("files", ("a.exe", b"MZ", "application/octet-stream")), ("files", ("b.txt", b"ok", "text/plain"))],
        headers=ACME,
    )
    body = r.json()
    assert body["accepted"] == 1 and body["rejected"][0]["status"] == 415
    assert len(queued) == 1


# ---- documents -----------------------------------------------------------------------------
def test_document_list_get_and_delete_are_limited_to_the_tenant(env):
    client, _, _ = env
    seed("acme", "plan.txt")
    seed("globex", "plan.txt")  # same file name, other company

    acme_docs = client.get("/documents", headers=ACME).json()["documents"]
    assert [d["document_id"] for d in acme_docs] == ["acme__plan.txt"]
    assert [d["document_id"] for d in client.get("/documents", headers=GLOBEX).json()["documents"]] == ["globex__plan.txt"]

    assert client.get("/documents/globex__plan.txt", headers=ACME).status_code == 404
    assert client.delete("/documents/globex__plan.txt", headers=ACME).status_code == 404
    assert len(client.get("/documents/globex__plan.txt", headers=GLOBEX).json()["chunks"]) >= 1

    assert client.delete("/documents/acme__plan.txt", headers=ACME).json()["deleted"] is True
    assert client.get("/documents", headers=ACME).json()["total_documents"] == 0


def test_reuploading_a_file_replaces_it(env):
    client, _, _ = env
    seed("acme", "notes.txt", "word " * 9000)  # over the single-chunk limit -> several chunks
    first = client.get("/documents", headers=ACME).json()["documents"][0]["chunk_count"]
    assert first > 1
    seed("acme", "notes.txt", "short now")
    docs = client.get("/documents", headers=ACME).json()["documents"]
    assert len(docs) == 1 and docs[0]["chunk_count"] == 1


# ---- the worker ----------------------------------------------------------------------------
def test_worker_refuses_a_job_with_no_tenant(env, tmp_path):
    _, fake, _ = env
    folder = tmp_path / "job1"
    folder.mkdir()
    (folder / "x.txt").write_text("secret")
    jobs.create_job("job1", "x.txt", None, tenant_id="", uploaded_by="")

    result = tasks.process_document.apply(args=["job1", str(folder / "x.txt")]).get()

    assert result["status"] == "failed"
    assert fake.hashes["ing:job:job1"]["status"] == "failed"
    assert not any(d["document_id"].endswith("x.txt") for d in api.orchestrator.qdrant_store.list_documents())
