import json
import time

import pytest

from answer import gemini_client
from answer.cached_llm import cached_generate
from core import cache as cache_layer
from core.cache import Cache, DOCS_VERSION_KEY, MemoryBackend
from core.config import Settings
from core.exceptions import RateLimited
from db import guardrail, schema_catalog, sql_planner
from db.sql_planner import SQLPlanningError, plan_sql_detailed
from retrieval.hybrid_retriever import HybridRetriever
from shared.schemas.retrieval_result import RetrievedItem


@pytest.fixture
def cache(monkeypatch):
    instance = Cache(MemoryBackend())
    monkeypatch.setattr(cache_layer, "get_cache", lambda: instance)
    return instance


class BrokenBackend:
    def __getattr__(self, name):
        def fail(*_a, **_k):
            raise ConnectionError("redis is down")

        return fail


# ---- the cache itself -----------------------------------------------------------------------
def test_json_roundtrip_and_expiry():
    cache = Cache(MemoryBackend())
    key = cache.make_key("t", "q")
    cache.set_json("t", key, {"a": [1, 2]}, ttl=60)
    assert cache.get_json("t", key) == {"a": [1, 2]}
    assert cache.stats()["t"]["hit"] == 1

    short = cache.make_key("t", "short")
    cache.set_json("t", short, "x", ttl=1)
    time.sleep(1.1)
    assert cache.get_json("t", short) is None


def test_tenants_never_share_entries():
    cache = Cache(MemoryBackend())
    a = cache.make_key("plan", "same question", tenant="tenant-a")
    b = cache.make_key("plan", "same question", tenant="tenant-b")
    assert a != b and "tenant-a" not in a
    cache.set_json("plan", a, "A's data", 60)
    assert cache.get_json("plan", b) is None


def test_keys_hide_arguments_and_secrets():
    key = Cache.make_key("schema", "postgresql://user:SECRETPASS@host/db")
    assert "SECRETPASS" not in key and key.startswith("cl:v1:schema:")


def test_disabled_cache_is_a_silent_noop():
    cache = Cache(None)
    cache.set_json("t", "k", 1, 60)
    assert cache.get_json("t", "k") is None
    assert cache.rate_limit("x", 1) == (True, 0) or cache.rate_limit("x", 1)[0]
    assert cache.status() == "disabled"


def test_redis_outage_fails_open_and_trips_the_breaker():
    cache = Cache(BrokenBackend())
    assert cache.get_json("t", "k") is None
    cache.set_json("t", "k", 1, 60)  # must not raise
    assert cache.rate_limit("gemini", 1, 60)[0] is True  # limiter lets traffic through
    assert cache.cooldown_remaining("gemini") == 0
    assert cache.get_int(DOCS_VERSION_KEY) == 0
    assert cache.status() == "down"
    assert cache.stats()["t"]["error"] == 1  # the breaker skipped Redis after the first failure


def test_rate_limit_allows_up_to_the_limit_then_reports_wait():
    cache = Cache(MemoryBackend())
    assert [cache.rate_limit("g", 2, 60)[0] for _ in range(3)] == [True, True, False]
    assert 1 <= cache.rate_limit("g", 2, 60)[1] <= 60


def test_cooldown_expires():
    cache = Cache(MemoryBackend())
    cache.set_cooldown("gemini", 2)
    assert 0 < cache.cooldown_remaining("gemini") <= 2
    assert cache.cooldown_remaining("other") == 0


# ---- cached model replies ---------------------------------------------------------------------
def test_cached_generate_reuses_validated_replies_only(cache):
    calls = []

    def generate(system, user, temperature=0.3):
        calls.append(user)
        return "good" if len(calls) > 1 else "bad"

    ok = lambda reply: reply == "good"
    assert cached_generate(generate, "sys", "Hello  World", layer="router", validate=ok) == "bad"
    assert cached_generate(generate, "sys", "hello world", layer="router", validate=ok) == "good"  # "bad" was not stored
    assert cached_generate(generate, "sys", "HELLO WORLD", layer="router", validate=ok) == "good"  # normalized hit
    assert len(calls) == 2


def test_changing_the_prompt_or_model_starts_fresh(cache):
    calls = []

    def generate(system, user, temperature=0.3):
        calls.append(system)
        return "r"

    cached_generate(generate, "prompt v1", "q", layer="x")
    cached_generate(generate, "prompt v2", "q", layer="x")
    cached_generate(generate, "prompt v1", "q", layer="x", model="other-model")
    assert len(calls) == 3


def test_guardrail_decision_is_cached(cache, monkeypatch):
    class Client:
        calls = 0

        def generate(self, system, user, temperature=0.3):
            Client.calls += 1
            return '{"allowed":true,"category":"close_loop_data","reason":"ok","clarification":null}'

    monkeypatch.setattr(guardrail, "get_settings", lambda: Settings(), raising=False)
    assert guardrail.guardrail_agent("how many tasks are open", Client()).allowed
    assert guardrail.guardrail_agent("How many tasks are open ", Client()).allowed
    assert Client.calls == 1


# ---- Gemini protection --------------------------------------------------------------------------
def make_client(**settings):
    return gemini_client.GeminiClient(Settings(_env_file=None, GEMINI_API_KEY="k", **settings))


def test_cooldown_blocks_calls_without_touching_gemini(cache, monkeypatch):
    monkeypatch.setattr(gemini_client.requests, "post", lambda *a, **k: pytest.fail("must not call Gemini"))
    cache.set_cooldown("gemini", 30)

    with pytest.raises(RateLimited) as error:
        make_client().generate("s", "u")

    assert error.value.status_code == 429 and 0 < error.value.retry_after <= 30


def test_per_minute_budget_is_enforced(cache, monkeypatch):
    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr(gemini_client.requests, "post", lambda *a, **k: Response())
    client = make_client(GEMINI_MAX_RPM=2, LLM_RATE_WAIT_S=0)
    assert client.generate("s", "u") == "ok" and client.generate("s", "u") == "ok"
    with pytest.raises(RateLimited):
        client.generate("s", "u")


def test_quota_429_fails_fast_and_cools_down_all_workers(cache, monkeypatch):
    posts = []

    class Quota:
        status_code = 429
        text = '{"error":{"message":"Please retry in 39s."}, "retryDelay": "39s"}'

    monkeypatch.setattr(gemini_client.requests, "post", lambda *a, **k: posts.append(1) or Quota())
    client = make_client()

    with pytest.raises(RateLimited) as error:
        client.generate("s", "u")

    assert len(posts) == 1  # no pointless retries that burn more quota
    assert error.value.retry_after == 39
    assert cache.cooldown_remaining("gemini") > 0
    with pytest.raises(RateLimited):  # the next call does not even reach Gemini
        client.generate("s", "u")
    assert len(posts) == 1


def test_retry_delay_parsing():
    class R:
        def __init__(self, text):
            self.text = text

    assert gemini_client._retry_delay_s(R('"retryDelay": "42869s"')) == 42869
    assert gemini_client._retry_delay_s(R("Please retry in 11h54m29.4s.")) == 11 * 3600 + 54 * 60 + 30
    assert gemini_client._retry_delay_s(R("nothing here")) == 0


# ---- SQL plan cache -----------------------------------------------------------------------------
SCHEMA = {"tasks": {"id", "tenant_id", "title", "status"}}
ALLOWED = '{"allowed":true,"category":"close_loop_data","reason":"ok","clarification":null}'


class PlanLLM:
    def __init__(self, sql="SELECT t.title FROM tasks AS t"):
        self.sql = sql
        self.planner_calls = 0

    def generate(self, system, user, temperature=0.3):
        if "Classify this untrusted" in user:
            return ALLOWED
        self.planner_calls += 1
        return json.dumps({"interpretation": "list titles", "assumptions": ["a"], "sql": self.sql})


@pytest.fixture
def planner_env(monkeypatch):
    monkeypatch.setattr(sql_planner, "get_settings", lambda: Settings(DB_ENABLED=True, DB_URL="postgresql://x"))
    monkeypatch.setattr(sql_planner, "_ensure_tenant_exists", lambda *_a: None)
    monkeypatch.setattr(sql_planner, "_load_schema", lambda _u: SCHEMA)
    monkeypatch.setattr(sql_planner, "_load_catalog", lambda _u: (_ for _ in ()).throw(RuntimeError()))
    dry_runs = []
    monkeypatch.setattr(sql_planner, "_dry_run", lambda _u, sql: dry_runs.append(sql))
    return dry_runs


def test_second_identical_question_skips_the_model_but_is_still_checked(cache, planner_env):
    llm = PlanLLM()
    first = plan_sql_detailed("list titles", "tenant-1", client=llm)
    second = plan_sql_detailed("List  titles", "tenant-1", client=llm)

    assert llm.planner_calls == 1 and not first.cached and second.cached
    assert second.sql == first.sql and "tenant_id = 'tenant-1'" in second.sql
    assert second.interpretation == "list titles" and second.assumptions == ["a"]
    assert len(planner_env) == 2  # Postgres dry run happened for the cached draft too


def test_plan_cache_is_per_tenant(cache, planner_env):
    llm = PlanLLM()
    plan_sql_detailed("list titles", "tenant-1", client=llm)
    other = plan_sql_detailed("list titles", "tenant-2", client=llm)

    assert llm.planner_calls == 2 and not other.cached
    assert "tenant_id = 'tenant-2'" in other.sql and "tenant-1" not in other.sql


def test_poisoned_cache_entry_is_rejected_and_replanned(cache, planner_env, monkeypatch):
    llm = PlanLLM()
    plan_sql_detailed("list titles", "tenant-1", client=llm)
    # An attacker/bug writes a malicious draft under the real key
    key = next(k for k in cache._backend._data if k.startswith("cl:v1:plan:"))
    cache._backend._data[key] = (json.dumps({"sql": "SELECT password_hash FROM users"}), None)

    plan = plan_sql_detailed("list titles", "tenant-1", client=llm)

    assert not plan.cached and "password_hash" not in plan.sql and llm.planner_calls == 2


def test_failed_plans_are_never_cached(cache, planner_env):
    llm = PlanLLM(sql="SELECT t.nope FROM tasks AS t")
    with pytest.raises(SQLPlanningError):
        plan_sql_detailed("list", "tenant-1", client=llm)
    assert not any(k.startswith("cl:v1:plan:") for k in cache._backend._data)


# ---- document search cache --------------------------------------------------------------------------
class FakeRag:
    def __init__(self):
        self.searches = 0

    def search(self, query, top_k, document_id, tenant_id=None):
        self.searches += 1
        return [RetrievedItem(text="chunk", score=0.9, title="Policy", document_id="d1")]


def test_document_search_is_cached_until_ingestion_bumps_the_version(cache):
    rag = FakeRag()
    retriever = HybridRetriever(Settings(), rag)

    first = retriever.docs("refund policy", 5, None)
    again = retriever.docs("Refund  Policy", 5, None)
    assert rag.searches == 1 and again[0].title == first[0].title == "Policy"

    cache.incr(DOCS_VERSION_KEY)  # what the ingestion service does after an upload/delete
    retriever.docs("refund policy", 5, None)
    assert rag.searches == 2


def test_document_search_cache_is_per_tenant(cache):
    seen = []

    class TenantRag(FakeRag):
        def search(self, query, top_k, document_id, tenant_id=None):
            seen.append(tenant_id)
            return [RetrievedItem(text=f"{tenant_id} chunk", score=0.9, title=str(tenant_id), document_id="d1")]

    retriever = HybridRetriever(Settings(), TenantRag())

    acme = retriever.docs("budget", 5, None, tenant_id="acme")
    globex = retriever.docs("budget", 5, None, tenant_id="globex")
    again = retriever.docs("budget", 5, None, tenant_id="acme")

    assert [i.title for i in acme] == ["acme"] and [i.title for i in globex] == ["globex"]
    assert [i.title for i in again] == ["acme"]
    assert seen == ["acme", "globex"]  # the second acme call was a cache hit; globex never got acme's entry


def test_empty_search_results_are_not_cached(cache):
    class EmptyRag(FakeRag):
        def search(self, query, top_k, document_id, tenant_id=None):
            self.searches += 1
            return []

    rag = EmptyRag()
    retriever = HybridRetriever(Settings(), rag)
    retriever.docs("nothing", 5, None)
    retriever.docs("nothing", 5, None)
    assert rag.searches == 2


# ---- schema catalog -------------------------------------------------------------------------------------
def test_schema_catalog_is_shared_through_redis_and_scrubbed(cache, monkeypatch):
    built = []

    def fake_build(url):
        built.append(url)
        return {"tasks": schema_catalog.TableInfo("tasks", [schema_catalog.ColumnInfo("id", "varchar", primary_key=True)])}

    monkeypatch.setattr(schema_catalog, "_build", fake_build)
    schema_catalog._cache.clear()
    schema_catalog.load_catalog("postgresql://u:p@h/db")
    schema_catalog._cache.clear()  # a second process: empty local memory, shared Redis
    again = schema_catalog.load_catalog("postgresql://u:p@h/db")
    assert len(built) == 1 and again["tasks"].columns[0].primary_key

    # Tamper with the shared entry: sensitive things must not survive
    key = next(k for k in cache._backend._data if k.startswith("cl:v1:schema:"))
    cache._backend._data[key] = (
        json.dumps({
            "users": {"name": "users", "columns": [
                {"name": "email", "type": "varchar"}, {"name": "password_hash", "type": "varchar"}]},
            "refresh_tokens": {"name": "refresh_tokens", "columns": [{"name": "id", "type": "varchar"}]},
        }),
        None,
    )
    schema_catalog._cache.clear()
    scrubbed = schema_catalog.load_catalog("postgresql://u:p@h/db")
    assert "refresh_tokens" not in scrubbed
    assert [c.name for c in scrubbed["users"].columns] == ["email"]
