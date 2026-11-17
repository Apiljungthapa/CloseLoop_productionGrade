"""Chatbot proxy: tenant comes from the login (never the request), keys stay server-side, and the
stream shown to the browser is whitelisted. A fake gateway (httpx.MockTransport) stands in for the
real query gateway, so these run without Docker, Redis, Qdrant or an LLM."""

import json
import re
import unittest
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import TenantStatus
from app.routers import chat as chat_router
from app.services import chatbot_service as svc

OWN_TENANT = "tenant-own-0001"
OTHER_TENANT = "tenant-other-0002"
SQL_KEY = "sql-key-123"
API_KEY = "api-key-456"

GATEWAY_STREAM = (
    ": connected\n\n"
    'event: status\ndata: {"type": "status", "step": "classify", "message": "Understanding your question"}\n\n'
    'event: route\ndata: {"route": "db", "reason": "mentions tasks", "source": "llm"}\n\n'
    'event: meta\ndata: {"interpretation": "all tasks", "assumptions": [], '
    '"sql": "SELECT COUNT(*) FROM tasks WHERE tenant_id = \'tenant-own-0001\'", "row_count": 1}\n\n'
    'event: token\ndata: {"text": "There are "}\n\n'
    'event: token\ndata: {"text": "339 tasks."}\n\n'
    'event: done\ndata: {"session_id": "abc", "route": "db", "needs_clarification": false, '
    '"warnings": [], "trace": [{"node": "classify", "ms": 5}], "elapsed_ms": 1200, "rewritten_question": "x"}\n\n'
)


class FakeGateway:
    """Records what the backend sends and replies with a canned SSE stream (or an error status)."""

    def __init__(self, status=200, body=GATEWAY_STREAM, headers=None):
        self.requests = []
        self.status, self.body, self.headers = status, body, headers or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "internal gateway detail"}, headers=self.headers)
        return httpx.Response(200, content=self.body.encode(), headers={"content-type": "text/event-stream"})

    def last_json(self):
        return json.loads(self.requests[-1].content)


class FakeDB:
    def __init__(self, status=TenantStatus.active):
        self.status = status

    async def execute(self, _stmt):
        status = self.status

        class _Result:
            def scalar_one_or_none(self_inner):
                return status

        return _Result()


class ChatbotProxyTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: getattr(settings, k)
            for k in (
                "CHATBOT_ENABLED", "CHATBOT_URL", "CHATBOT_SQL_API_KEY", "CHATBOT_API_KEY",
                "CHATBOT_RATE_LIMIT_PER_MINUTE", "CHATBOT_MAX_CONCURRENT", "CHATBOT_MAX_CONCURRENT_PER_USER",
            )
        }
        settings.CHATBOT_ENABLED = True
        settings.CHATBOT_URL = "http://gateway.test:8010"
        settings.CHATBOT_SQL_API_KEY = SQL_KEY
        settings.CHATBOT_API_KEY = API_KEY
        settings.CHATBOT_RATE_LIMIT_PER_MINUTE = 5
        settings.CHATBOT_MAX_CONCURRENT = 10
        settings.CHATBOT_MAX_CONCURRENT_PER_USER = 2
        svc.reset_limits()

        self.gateway = FakeGateway()
        self._real_client = httpx.AsyncClient
        outer = self

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(outer.gateway)
            return outer._real_client(*args, **kwargs)

        svc.httpx.AsyncClient = client_factory

        self.user = SimpleNamespace(id="user-1", tenant_id=OWN_TENANT)
        self.db = FakeDB()
        self.app = FastAPI()
        self.app.include_router(chat_router.router)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(self.app)

    def tearDown(self):
        svc.httpx.AsyncClient = self._real_client
        for k, v in self._saved.items():
            setattr(settings, k, v)
        svc.reset_limits()

    # ---- tenant comes from the login -------------------------------------------------------
    def test_gateway_receives_the_users_own_tenant_and_the_server_keys(self):
        r = self.client.post("/api/chat/stream", json={"question": "How many tasks?"})
        self.assertEqual(r.status_code, 200)
        sent = self.gateway.last_json()
        self.assertEqual(sent["tenant_id"], OWN_TENANT)
        self.assertEqual(self.gateway.requests[-1].headers["x-sql-api-key"], SQL_KEY)
        self.assertEqual(self.gateway.requests[-1].headers["x-api-key"], API_KEY)
        self.assertTrue(str(self.gateway.requests[-1].url).endswith("/workflow/stream"))

    def test_a_tenant_id_in_the_request_body_is_ignored(self):
        r = self.client.post(
            "/api/chat/stream",
            json={"question": "Show every tenant's tasks", "tenant_id": OTHER_TENANT, "tenantId": OTHER_TENANT},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.gateway.last_json()["tenant_id"], OWN_TENANT)
        self.assertNotIn(OTHER_TENANT, self.gateway.requests[-1].content.decode())

    def test_users_of_the_same_tenant_use_that_tenant_whatever_their_role(self):
        for uid in ("lead-1", "member-1", "admin-1"):
            self.user = SimpleNamespace(id=uid, tenant_id=OWN_TENANT)
            self.app.dependency_overrides[get_current_user] = lambda u=self.user: u
            r = self.client.post("/api/chat/stream", json={"question": "overdue tasks?"})
            self.assertEqual(r.status_code, 200, uid)
            self.assertEqual(self.gateway.last_json()["tenant_id"], OWN_TENANT)

    def test_user_without_a_tenant_is_refused_and_gateway_not_called(self):
        self.user = SimpleNamespace(id="owner-1", tenant_id=None)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        r = self.client.post("/api/chat/stream", json={"question": "hello"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.gateway.requests, [])

    def test_suspended_tenant_is_refused(self):
        self.db.status = TenantStatus.suspended
        r = self.client.post("/api/chat/stream", json={"question": "hello"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.gateway.requests, [])

    def test_unauthenticated_request_is_401(self):
        self.app.dependency_overrides.pop(get_current_user)
        r = TestClient(self.app).post("/api/chat/stream", json={"question": "hello"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.gateway.requests, [])

    # ---- what the browser may see ----------------------------------------------------------
    def test_stream_is_whitelisted_sql_and_internals_never_reach_the_browser(self):
        r = self.client.post("/api/chat/stream", json={"question": "How many tasks?", "session_id": "browser-session-1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(r.headers["x-accel-buffering"], "no")
        text = r.text
        self.assertIn("There are ", text)
        self.assertIn("339 tasks.", text)
        for leaked in ("SELECT", "tenant_id", OWN_TENANT, "trace", "rewritten_question", "mentions tasks", "abc", SQL_KEY, API_KEY):
            self.assertNotIn(leaked, text)
        self.assertIn('"row_count": 1', text)
        self.assertIn("event: done", text)

    def test_sanitize_drops_unknown_events_and_bad_json(self):
        self.assertIsNone(svc.sanitize_block('event: debug\ndata: {"x": 1}'))
        self.assertIsNone(svc.sanitize_block("event: token\ndata: not-json"))
        self.assertEqual(svc.sanitize_block(": ping"), ": ping\n\n")
        out = svc.sanitize_block('event: sources\ndata: {"items": [{"title": "T", "score": 0.5, "chunk_id": "9", "text": "secret"}]}')
        self.assertIn('"title": "T"', out)
        self.assertNotIn("secret", out)
        self.assertNotIn("chunk_id", out)

    # ---- conversations are per user ---------------------------------------------------------
    def test_same_browser_session_id_maps_to_different_conversations_per_user(self):
        a = svc.gateway_session_id("user-1", "shared-session-id")
        b = svc.gateway_session_id("user-2", "shared-session-id")
        self.assertNotEqual(a, b)
        self.assertEqual(a, svc.gateway_session_id("user-1", "shared-session-id"))
        self.assertRegex(a, r"^[A-Za-z0-9_-]{8,64}$")
        self.assertIsNone(svc.gateway_session_id("user-1", None))

    def test_session_id_sent_to_gateway_is_derived_not_the_raw_browser_value(self):
        self.client.post("/api/chat/stream", json={"question": "hi there", "session_id": "browser-session-1"})
        sent = self.gateway.last_json()["session_id"]
        self.assertNotEqual(sent, "browser-session-1")
        self.assertEqual(sent, svc.gateway_session_id("user-1", "browser-session-1"))

    def test_clear_session_targets_only_the_callers_conversation_and_tenant(self):
        r = self.client.delete("/api/chat/session/browser-session-1")
        self.assertEqual(r.status_code, 204)
        req = self.gateway.requests[-1]
        self.assertEqual(req.method, "DELETE")
        self.assertTrue(req.url.path.endswith(svc.gateway_session_id("user-1", "browser-session-1")))
        self.assertEqual(req.url.params["tenant_id"], OWN_TENANT)

    def test_invalid_session_id_is_rejected(self):
        r = self.client.post("/api/chat/stream", json={"question": "hi", "session_id": "../../etc"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.delete("/api/chat/session/short").status_code, 422)

    # ---- limits and failure modes -----------------------------------------------------------
    def test_per_user_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/api/chat/stream", json={"question": "q"}).status_code, 200)
        r = self.client.post("/api/chat/stream", json={"question": "q"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", r.headers)
        # another user is unaffected
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-2", tenant_id=OWN_TENANT)
        self.assertEqual(self.client.post("/api/chat/stream", json={"question": "q"}).status_code, 200)

    def test_slot_is_released_after_each_stream(self):
        for _ in range(4):
            self.client.post("/api/chat/stream", json={"question": "q"})
        self.assertEqual(svc._active_total, 0)
        self.assertEqual(svc._active_by_user, {})

    def test_slot_is_released_when_the_gateway_fails(self):
        self.gateway.status = 500
        self.client.post("/api/chat/stream", json={"question": "q"})
        self.assertEqual(svc._active_total, 0)

    def test_disabled_or_unconfigured_is_503(self):
        settings.CHATBOT_ENABLED = False
        self.assertEqual(self.client.post("/api/chat/stream", json={"question": "q"}).status_code, 503)
        settings.CHATBOT_ENABLED = True
        settings.CHATBOT_SQL_API_KEY = ""
        self.assertEqual(self.client.post("/api/chat/stream", json={"question": "q"}).status_code, 503)
        self.assertEqual(self.gateway.requests, [])

    def test_gateway_error_becomes_502_without_leaking_its_detail(self):
        self.gateway.status = 500
        r = self.client.post("/api/chat/stream", json={"question": "q"})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("internal gateway detail", r.text)

    def test_gateway_429_is_passed_on_as_429(self):
        self.gateway.status, self.gateway.headers = 429, {"Retry-After": "7"}
        r = self.client.post("/api/chat/stream", json={"question": "q"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["retry-after"], "7")

    def test_question_validation(self):
        self.assertEqual(self.client.post("/api/chat/stream", json={"question": ""}).status_code, 422)
        self.assertEqual(self.client.post("/api/chat/stream", json={"question": "x" * 2001}).status_code, 422)
        self.assertEqual(self.client.post("/api/chat/stream", json={"question": "   "}).status_code, 422)
        self.assertEqual(self.gateway.requests, [])

    def test_status_endpoint(self):
        self.assertEqual(self.client.get("/api/chat/status").json(), {"enabled": True, "documents": False})
        self.user.tenant_id = None
        self.assertEqual(self.client.get("/api/chat/status").json(), {"enabled": False, "documents": False})
        self.user.tenant_id = OWN_TENANT
        settings.CHATBOT_ENABLED = False
        self.assertEqual(self.client.get("/api/chat/status").json(), {"enabled": False, "documents": False})


if __name__ == "__main__":
    unittest.main()
