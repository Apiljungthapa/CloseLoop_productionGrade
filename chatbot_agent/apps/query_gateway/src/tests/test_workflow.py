import threading

import pytest

from answer.repair_loop import CHITCHAT_REPLY, NOT_FOUND_ANSWER
from core.exceptions import LLMUnavailable, RetrievalUnavailable
from db.sql_planner import ClarificationNeeded, SQLPlanningError
from shared.constants.enums import EvidenceSource
from shared.schemas.retrieval_result import RetrievedItem
from workflow.workflow import DbResult, WorkflowDeps, build_workflow, run_workflow


def route_reply(route, doc_query=None, db_query=None):
    import json

    return json.dumps(
        {"route": route, "reason": f"test {route}", "doc_query": doc_query, "db_query": db_query}
    )


class FakeGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, question, evidence, extra_instruction=""):
        self.calls.append((question, evidence))
        return "final answer"


class Harness:
    """Fakes for every collaborator, recording what the graph called."""

    def __init__(self, router="doc", doc_hits=1, db_result="ok", barrier=False, router_error=None):
        self.router = router
        self.router_error = router_error
        self.doc_calls, self.db_calls, self.doc_tenants = [], [], []
        self.generator = FakeGenerator()
        self.doc_hits = doc_hits
        self.db_result = db_result
        self.barrier = threading.Barrier(2, timeout=3) if barrier else None

    def llm_generate(self, system, user, temperature=0.3):
        if self.router_error:
            raise self.router_error
        return self.router

    def search_docs(self, query, top_k, tenant_id=None):
        self.doc_calls.append(query)
        self.doc_tenants.append(tenant_id)
        if self.barrier:
            self.barrier.wait()
        return [
            RetrievedItem(text=f"doc chunk {i}", score=0.8 - i / 10, title="Policy", document_id="d1")
            for i in range(self.doc_hits)
        ]

    def run_database(self, question, tenant_id, max_rows):
        self.db_calls.append((question, tenant_id, max_rows))
        if self.barrier:
            self.barrier.wait()
        if isinstance(self.db_result, Exception):
            raise self.db_result
        return DbResult(
            "SELECT 1 /* scoped */", [{"name": "Binita", "open_tasks": 459}], "Count open tasks", ["rn = now"]
        )

    def graph(self, **overrides):
        deps = WorkflowDeps(
            llm_generate=self.llm_generate,
            search_docs=self.search_docs,
            run_database=self.run_database,
            answer_generator=self.generator,
            db_configured=True,
            **overrides,
        )
        return build_workflow(deps)


def run(harness, question="q", **kwargs):
    kwargs.setdefault("tenant_id", "tenant-1")
    kwargs.setdefault("db_allowed", True)
    return run_workflow(harness.graph(), question, **kwargs)


def nodes(state):
    # rewrite_question (follow-up resolution) always runs first; its own tests are in test_conversation.py
    return [step["node"] for step in state["trace"] if step["node"] != "rewrite_question"]


def test_chitchat_skips_all_retrieval():
    harness = Harness(router=route_reply("chitchat"))

    state = run(harness, "hello there")

    assert state["answer"] == CHITCHAT_REPLY
    assert harness.doc_calls == [] and harness.db_calls == []
    assert nodes(state) == ["classify", "chitchat"]


def test_doc_route_only_searches_documents():
    harness = Harness(router=route_reply("doc", doc_query="refund policy"))

    state = run(harness, "whats the refnd policy")

    assert harness.doc_calls == ["refund policy"] and harness.db_calls == []
    assert state["route"] == "doc" and state["answer"] == "final answer"
    assert nodes(state) == ["classify", "retrieve_docs", "merge", "generate"]


def test_document_search_is_scoped_to_the_callers_tenant():
    harness = Harness(router=route_reply("doc", doc_query="refund policy"))

    run(harness, "whats the refund policy")

    assert harness.doc_tenants == ["tenant-1"]


def test_db_route_only_queries_database_and_keeps_interpretation():
    harness = Harness(router=route_reply("db", db_query="open tasks per person"))

    state = run(harness, "who has most work", max_rows=25)

    assert harness.db_calls == [("open tasks per person", "tenant-1", 25)] and harness.doc_calls == []
    assert state["db_interpretation"] == "Count open tasks"
    evidence = harness.generator.calls[0][1]
    assert evidence[0].source == EvidenceSource.DATABASE
    assert "Question understood as: Count open tasks" in evidence[0].text
    assert "Assumptions: rn = now" in evidence[0].text
    assert "Today is" in evidence[0].text


def test_hybrid_runs_both_branches_in_parallel_and_merges_evidence():
    # Each branch waits for the other: sequential execution would time out the barrier.
    harness = Harness(router=route_reply("hybrid", "close process", "overdue tasks"), barrier=True)

    state = run(harness, "explain the close process and how many tasks are overdue")

    assert harness.doc_calls == ["close process"]
    assert harness.db_calls == [("overdue tasks", "tenant-1", 50)]
    sources = {item.source for item in harness.generator.calls[0][1]}
    assert sources == {EvidenceSource.DOCUMENT, EvidenceSource.DATABASE}
    assert nodes(state).count("merge") == 1 and nodes(state)[-1] == "generate"
    assert state["warnings"] == []


def test_hybrid_survives_a_failed_database_branch():
    harness = Harness(router=route_reply("hybrid"), db_result=SQLPlanningError("bad column"))

    state = run(harness)

    assert state["answer"] == "final answer"
    assert any("bad column" in warning for warning in state["warnings"])
    assert {item.source for item in harness.generator.calls[0][1]} == {EvidenceSource.DOCUMENT}


def test_hybrid_survives_a_failed_document_branch():
    harness = Harness(router=route_reply("hybrid"))
    harness.search_docs = lambda *_a: (_ for _ in ()).throw(RetrievalUnavailable())

    state = run(harness)

    assert state["answer"] == "final answer"
    assert any("document store" in warning for warning in state["warnings"])
    assert {item.source for item in harness.generator.calls[0][1]} == {EvidenceSource.DATABASE}


def test_failed_database_only_question_falls_back_to_documents_once():
    harness = Harness(router=route_reply("db"), db_result=SQLPlanningError("nope"))

    state = run(harness, "tell me count")

    assert harness.doc_calls == ["tell me count"]
    assert nodes(state) == ["classify", "query_database", "merge", "retrieve_docs", "merge", "generate"]


def test_failed_database_and_empty_documents_gives_not_found():
    harness = Harness(router=route_reply("db"), db_result=SQLPlanningError("nope"), doc_hits=0)

    state = run(harness)

    assert state["answer"] == NOT_FOUND_ANSWER
    assert len(harness.doc_calls) == 1  # the fallback runs only once


def test_database_not_allowed_degrades_to_documents_with_a_warning():
    harness = Harness(router=route_reply("hybrid"))

    state = run(harness, db_allowed=False)

    assert harness.db_calls == [] and state["route"] == "doc"
    assert any("not enabled" in warning for warning in state["warnings"])


def test_database_question_without_tenant_degrades_to_documents():
    harness = Harness(router=route_reply("db"))

    state = run(harness, tenant_id=None)

    assert harness.db_calls == [] and state["route"] == "doc"
    assert any("tenant_id" in warning for warning in state["warnings"])


def test_router_outage_falls_back_to_keyword_rules():
    harness = Harness(router_error=LLMUnavailable())

    state = run(harness, "how many tasks are overdue")

    assert state["route_source"] == "rules" and state["route"] == "db"
    assert any("unavailable" in warning for warning in state["warnings"])
    assert len(harness.db_calls) == 1


def test_unreadable_router_reply_falls_back_to_rules():
    harness = Harness(router="I think it is a database question")

    state = run(harness, "explain the close process")

    assert state["route_source"] == "rules" and state["route"] == "doc"


def test_rules_only_mode_never_calls_the_router_model():
    harness = Harness(router_error=AssertionError("router model must not be called"))

    state = run_workflow(
        harness.graph(llm_routing=False), "how many tasks", tenant_id="t", db_allowed=True
    )

    assert state["route_source"] == "rules"


def test_database_clarification_is_returned_without_generating():
    harness = Harness(router=route_reply("db"), db_result=ClarificationNeeded("Which period?"))
    harness.doc_hits = 0

    state = run(harness, "show it")

    assert state["answer"] == "Which period?"
    assert harness.generator.calls == []
    assert nodes(state)[-1] == "clarify"


def test_hybrid_with_clarification_still_answers_from_documents_and_asks():
    harness = Harness(router=route_reply("hybrid"), db_result=ClarificationNeeded("Which period?"))

    state = run(harness)

    assert state["answer"].startswith("final answer")
    assert "Which period?" in state["answer"]


def test_no_evidence_returns_safe_not_found():
    harness = Harness(router=route_reply("doc"), doc_hits=0)

    state = run(harness)

    assert state["answer"] == NOT_FOUND_ANSWER and harness.generator.calls == []
    assert nodes(state)[-1] == "no_evidence"


def test_database_branch_needs_every_switch_and_the_shared_key():
    from app.routes.workflow import database_allowed
    from core.config import Settings

    on = dict(
        DB_ENABLED=True,
        DB_URL="postgresql://x",
        SQL_PLANNER_API_ENABLED=True,
        SQL_QUERY_EXECUTION_ENABLED=True,
        SQL_PLANNER_API_KEY="secret",
    )
    assert database_allowed(Settings(**on), "secret")
    assert not database_allowed(Settings(**on), None)
    assert not database_allowed(Settings(**on), "wrong")
    for switch in ("DB_ENABLED", "SQL_PLANNER_API_ENABLED", "SQL_QUERY_EXECUTION_ENABLED"):
        assert not database_allowed(Settings(**{**on, switch: False}), "secret")
    assert not database_allowed(Settings(**{**on, "SQL_PLANNER_API_KEY": ""}), "")
