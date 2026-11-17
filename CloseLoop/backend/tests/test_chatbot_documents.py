"""Chatbot document upload: the tenant comes from the login (never the request), the ingestion key
stays server-side, and a user can only list, upload or delete their own organization's documents.
A fake ingestion API (httpx.MockTransport) stands in for the real one, so these run without Docker,
Qdrant, RabbitMQ or an embedding model."""

import json
import re
import unittest
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Role, TenantStatus
from app.routers import chat as chat_router
from app.services import chatbot_documents as docs

OWN = "tenant-own-0001"
OTHER = "tenant-other-0002"
INGEST_KEY = "ingest-key-789"


class FakeIngestion:
    """A tiny stand-in for the ingestion API that, like the real one, acts only for X-Tenant-Id."""

    def __init__(self):
        self.requests = []
        self.docs = {OWN: {}, OTHER: {}}  # tenant -> {document_id: {"uploaded_by", "chunks", "format"}}
        self.status_override = None
        self.batch = {"total": 2, "finished": True, "counts": {"done": 1, "failed": 1}, "jobs": []}

    def add(self, tenant, name, uploaded_by="", chunks=3):
        self.docs.setdefault(tenant, {})[f"{tenant}__{name}"] = {
            "uploaded_by": uploaded_by, "chunks": chunks, "format": name.rsplit(".", 1)[-1],
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status_override:
            return httpx.Response(self.status_override, json={"detail": "internal ingestion detail"})
        if request.headers.get("x-api-key") != INGEST_KEY:
            return httpx.Response(401, json={"detail": "bad key"})
        tenant = request.headers.get("x-tenant-id")
        if not tenant:
            return httpx.Response(400, json={"detail": "tenant required"})
        mine = self.docs.setdefault(tenant, {})
        path, method = request.url.path, request.method

        if method == "GET" and path == "/documents":
            rows = [
                {"document_id": i, "title": i, "file_format": d["format"], "chunk_count": d["chunks"],
                 "uploaded_by": d["uploaded_by"] or None}
                for i, d in mine.items()
            ]
            return httpx.Response(200, json={"total_documents": len(rows), "documents": rows})
        if method == "GET" and path.startswith("/documents/"):
            return self._get_one(request, mine)
        if method == "DELETE" and path.startswith("/documents/"):
            doc_id = self._doc_id(request)
            if doc_id not in mine:
                return httpx.Response(404, json={"detail": "not found"})
            del mine[doc_id]
            return httpx.Response(200, json={"document_id": doc_id, "deleted": True})
        if method == "POST" and path == "/upload/batch":
            names = re.findall(rb'filename="([^"]+)"', request.content)
            accepted, rejected = [], []
            for raw in names:
                name = raw.decode()
                if name.endswith(".exe"):
                    rejected.append({"filename": name, "status": 415, "reason": "Unsupported extension"})
                else:
                    accepted.append(name)
                    self.add(tenant, name, request.headers.get("x-user-id", ""))
            return httpx.Response(202, json={
                "batch_id": "a" * 32, "accepted": len(accepted), "rejected": rejected,
                "jobs": [{"filename": n, "job_id": "j" * 32} for n in accepted],
            })
        if method == "GET" and path.startswith("/batches/"):
            return httpx.Response(200, json={"batch_id": path.rsplit("/", 1)[1], **self.batch})
        return httpx.Response(404, json={"detail": "unknown"})

    @staticmethod
    def _doc_id(request):
        return unquote(request.url.raw_path.decode().split("?")[0].removeprefix("/documents/"))

    def _get_one(self, request, mine):
        doc_id = self._doc_id(request)
        if doc_id not in mine:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json={
            "document_id": doc_id, "total_chunks": 1,
            "chunks": [{"chunk_id": 1, "uploaded_by": mine[doc_id]["uploaded_by"]}],
        })


class FakeDB:
    def __init__(self, status=TenantStatus.active):
        self.status = status

    async def execute(self, _stmt):
        status = self.status

        class _Result:
            def scalar_one_or_none(self_inner):
                return status

        return _Result()


def upload_files(*names):
    return [("files", (n, b"hello " + n.encode(), "text/plain")) for n in names]


class ChatbotDocumentTests(unittest.TestCase):
    def setUp(self):
        keys = (
            "CHATBOT_ENABLED", "CHATBOT_URL", "CHATBOT_SQL_API_KEY", "CHATBOT_API_KEY", "CHATBOT_INGEST_ENABLED",
            "CHATBOT_INGEST_URL", "CHATBOT_INGEST_API_KEY", "CHATBOT_MAX_DOCUMENTS", "CHATBOT_MAX_FILES_PER_UPLOAD",
        )
        self._saved = {k: getattr(settings, k) for k in keys}
        settings.CHATBOT_ENABLED = True
        settings.CHATBOT_URL = "http://gateway.test:8010"
        settings.CHATBOT_SQL_API_KEY = "sql"
        settings.CHATBOT_API_KEY = "api"
        settings.CHATBOT_INGEST_ENABLED = True
        settings.CHATBOT_INGEST_URL = "http://ingest.test:8001"
        settings.CHATBOT_INGEST_API_KEY = INGEST_KEY
        settings.CHATBOT_MAX_DOCUMENTS = 5
        settings.CHATBOT_MAX_FILES_PER_UPLOAD = 3

        self.ingest = FakeIngestion()
        self._real_client = httpx.AsyncClient
        outer = self

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(outer.ingest)
            return outer._real_client(*args, **kwargs)

        docs.httpx.AsyncClient = client_factory

        self.user = SimpleNamespace(id="user-1", tenant_id=OWN, role=Role.TEAM_MEMBER)
        self.db = FakeDB()
        self.app = FastAPI()
        self.app.include_router(chat_router.router)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(self.app)

    def tearDown(self):
        docs.httpx.AsyncClient = self._real_client
        for k, v in self._saved.items():
            setattr(settings, k, v)

    def as_user(self, **kw):
        self.user = SimpleNamespace(**{"id": "user-1", "tenant_id": OWN, "role": Role.TEAM_MEMBER, **kw})
        self.app.dependency_overrides[get_current_user] = lambda: self.user

    # ---- the tenant comes from the login ---------------------------------------------------
    def test_upload_acts_for_the_users_own_tenant_with_the_server_key(self):
        r = self.client.post("/api/chat/documents", files=upload_files("budget.pdf"))
        self.assertEqual(r.status_code, 202)
        sent = self.ingest.requests[-1]
        self.assertEqual(sent.headers["x-tenant-id"], OWN)
        self.assertEqual(sent.headers["x-user-id"], "user-1")
        self.assertEqual(sent.headers["x-api-key"], INGEST_KEY)
        self.assertTrue(str(sent.url).endswith("/upload/batch"))
        self.assertEqual(r.json()["accepted"], 1)
        self.assertEqual(r.json()["batch_id"], "a" * 32)

    def test_a_tenant_in_the_request_is_ignored(self):
        r = self.client.post(
            "/api/chat/documents?tenant_id=" + OTHER,
            files=upload_files("budget.pdf"),
            data={"tenant_id": OTHER},
            headers={"X-Tenant-Id": OTHER},
        )
        self.assertEqual(r.status_code, 202)
        for req in self.ingest.requests:
            self.assertEqual(req.headers["x-tenant-id"], OWN)
            self.assertNotIn(OTHER, str(req.url))
            self.assertNotIn(OTHER.encode(), req.content)
        self.assertIn(f"{OWN}__budget.pdf", self.ingest.docs[OWN])
        self.assertEqual(self.ingest.docs[OTHER], {})

    def test_listing_shows_only_own_documents_without_the_tenant_prefix(self):
        self.ingest.add(OWN, "plan.pdf", uploaded_by="user-1")
        self.ingest.add(OWN, "notes.txt", uploaded_by="someone-else")
        self.ingest.add(OTHER, "secret.pdf", uploaded_by="x")
        body = self.client.get("/api/chat/documents").json()
        self.assertEqual([d["name"] for d in body["documents"]], ["notes.txt", "plan.pdf"])
        self.assertEqual({d["name"]: d["mine"] for d in body["documents"]}, {"plan.pdf": True, "notes.txt": False})
        self.assertNotIn("secret.pdf", json.dumps(body))
        self.assertEqual(body["limit"], 5)
        self.assertEqual(self.ingest.requests[-1].headers["x-tenant-id"], OWN)

    def test_user_without_a_tenant_or_with_a_suspended_one_is_refused(self):
        self.as_user(tenant_id=None)
        self.assertEqual(self.client.get("/api/chat/documents").status_code, 403)
        self.assertEqual(self.client.post("/api/chat/documents", files=upload_files("a.txt")).status_code, 403)
        self.as_user()
        self.db.status = TenantStatus.suspended
        self.assertEqual(self.client.get("/api/chat/documents").status_code, 403)
        self.assertEqual(self.ingest.requests, [])

    def test_unauthenticated_is_401(self):
        self.app.dependency_overrides.pop(get_current_user)
        self.assertEqual(TestClient(self.app).get("/api/chat/documents").status_code, 401)
        self.assertEqual(self.ingest.requests, [])

    # ---- switched off -----------------------------------------------------------------------
    def test_everything_is_unavailable_when_upload_is_not_enabled(self):
        settings.CHATBOT_INGEST_ENABLED = False
        self.assertEqual(self.client.get("/api/chat/status").json(), {"enabled": True, "documents": False})
        self.assertEqual(self.client.get("/api/chat/documents").status_code, 503)
        self.assertEqual(self.client.post("/api/chat/documents", files=upload_files("a.txt")).status_code, 503)
        self.assertEqual(self.ingest.requests, [])

    def test_status_reports_documents_when_enabled(self):
        self.assertEqual(self.client.get("/api/chat/status").json(), {"enabled": True, "documents": True})
        settings.CHATBOT_INGEST_API_KEY = ""  # no key -> never call ingestion unauthenticated
        self.assertEqual(self.client.get("/api/chat/status").json(), {"enabled": True, "documents": False})

    # ---- limits -----------------------------------------------------------------------------
    def test_too_many_files_in_one_request(self):
        r = self.client.post("/api/chat/documents", files=upload_files("1.txt", "2.txt", "3.txt", "4.txt"))
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.ingest.requests, [])

    def test_document_limit_counts_only_new_names(self):
        for n in ("a.txt", "b.txt", "c.txt", "d.txt"):
            self.ingest.add(OWN, n, "user-1")
        # one more new file fits (5 allowed) ...
        self.assertEqual(self.client.post("/api/chat/documents", files=upload_files("e.txt")).status_code, 202)
        # ... but a sixth does not
        r = self.client.post("/api/chat/documents", files=upload_files("f.txt"))
        self.assertEqual(r.status_code, 409)
        # replacing an existing file is always fine
        self.assertEqual(self.client.post("/api/chat/documents", files=upload_files("a.txt")).status_code, 202)

    def test_unsupported_files_are_reported_not_fatal(self):
        r = self.client.post("/api/chat/documents", files=upload_files("ok.txt", "virus.exe"))
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["accepted"], 1)
        self.assertEqual(r.json()["rejected"], [{"name": "virus.exe", "status": 415}])

    def test_no_file_is_422(self):
        self.assertEqual(self.client.post("/api/chat/documents").status_code, 422)

    # ---- progress ---------------------------------------------------------------------------
    def test_progress_is_whitelisted_and_failures_get_a_safe_reason(self):
        self.ingest.batch = {
            "total": 2, "finished": True, "counts": {"done": 1, "failed": 1},
            "jobs": [
                {"job_id": "j1", "filename": "ok.txt", "status": "done", "chunks": 3, "attempts": 1, "error": ""},
                {"job_id": "j2", "filename": "bad.pdf", "status": "failed", "chunks": 0, "attempts": 1,
                 "error": "PdfReadError: /srv/app/secret/path boom"},
            ],
        }
        r = self.client.get("/api/chat/documents/batches/" + "a" * 32)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["total"], body["done"], body["failed"], body["finished"]), (2, 1, 1, True))
        self.assertEqual(body["failures"][0]["name"], "bad.pdf")
        self.assertNotIn("secret", r.text)
        self.assertNotIn("j2", r.text)

    def test_progress_rejects_a_malformed_batch_id(self):
        self.assertEqual(self.client.get("/api/chat/documents/batches/not-a-batch").status_code, 422)

    # ---- delete -----------------------------------------------------------------------------
    def test_uploader_can_delete_their_own_document(self):
        self.ingest.add(OWN, "mine.txt", uploaded_by="user-1")
        r = self.client.delete(f"/api/chat/documents/{OWN}__mine.txt")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.ingest.docs[OWN], {})

    def test_other_members_cannot_delete_it_but_a_super_admin_can(self):
        self.ingest.add(OWN, "plan.txt", uploaded_by="someone-else")
        r = self.client.delete(f"/api/chat/documents/{OWN}__plan.txt")
        self.assertEqual(r.status_code, 403)
        self.assertIn(f"{OWN}__plan.txt", self.ingest.docs[OWN])

        self.as_user(role=Role.SUPER_ADMIN)
        self.assertEqual(self.client.delete(f"/api/chat/documents/{OWN}__plan.txt").status_code, 204)
        self.assertEqual(self.ingest.docs[OWN], {})

    def test_a_document_without_a_recorded_uploader_is_admin_only(self):
        self.ingest.add(OWN, "old.txt", uploaded_by="")
        self.assertEqual(self.client.delete(f"/api/chat/documents/{OWN}__old.txt").status_code, 403)

    def test_another_tenants_document_cannot_be_deleted(self):
        self.ingest.add(OTHER, "secret.pdf", uploaded_by="user-1")  # even if the user id happened to match
        before = len(self.ingest.requests)
        r = self.client.delete(f"/api/chat/documents/{OTHER}__secret.pdf")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(self.ingest.requests), before)  # refused before ingestion was even asked
        self.assertIn(f"{OTHER}__secret.pdf", self.ingest.docs[OTHER])

    def test_deleting_something_that_does_not_exist_is_404(self):
        self.assertEqual(self.client.delete(f"/api/chat/documents/{OWN}__nope.txt").status_code, 404)
        self.assertEqual(self.client.delete("/api/chat/documents/shared").status_code, 404)

    # ---- failures ---------------------------------------------------------------------------
    def test_ingestion_errors_become_502_without_leaking_detail(self):
        self.ingest.status_override = 500
        for r in (
            self.client.get("/api/chat/documents"),
            self.client.post("/api/chat/documents", files=upload_files("a.txt")),
        ):
            self.assertEqual(r.status_code, 502)
            self.assertNotIn("internal ingestion detail", r.text)

    def test_unreachable_ingestion_is_502(self):
        def boom(request):
            raise httpx.ConnectError("down")

        self.ingest = boom  # the transport closes over `outer.ingest`
        self.assertEqual(self.client.get("/api/chat/documents").status_code, 502)


if __name__ == "__main__":
    unittest.main()
