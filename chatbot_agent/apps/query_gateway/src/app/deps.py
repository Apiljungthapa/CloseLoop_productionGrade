"""Wiring: builds the long-lived services once and hands them to routes."""
import hmac
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Header, HTTPException

from answer.generator import AnswerGenerator
from core.config import Settings, get_settings
# from planner.query_planner import QueryPlanner  # disabled: no intent planning for now
from llm.client import LLMClient
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.rag_retriever import RagRetriever
from workflow.workflow import DbResult, WorkflowDeps, build_workflow


@dataclass
class Services:
    settings: Settings
    # planner: QueryPlanner  # disabled
    retriever: HybridRetriever
    generator: AnswerGenerator


@lru_cache
def get_services() -> Services:
    settings = get_settings()
    rag = RagRetriever(settings)
    return Services(
        settings=settings,
        # planner=QueryPlanner(db_enabled=settings.DB_ENABLED),  # disabled
        retriever=HybridRetriever(settings, rag),
        generator=AnswerGenerator(LLMClient(settings)),
    )


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforced only when API_AUTH_KEY is configured."""
    expected = get_settings().API_AUTH_KEY
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "Missing or invalid X-API-Key header.")


@lru_cache
def get_workflow():
    """Compile the LangGraph workflow once; it holds no per-request state."""
    from db import executor
    from db.sql_planner import plan_sql_detailed

    services = get_services()
    settings = services.settings
    llm = services.generator.client

    def run_database(question: str, tenant_id: str, max_rows: int) -> DbResult:
        plan = plan_sql_detailed(question, tenant_id=tenant_id, max_rows=max_rows, client=llm)
        rows = executor.execute(plan.sql, tenant_id=tenant_id, max_rows=max_rows)
        return DbResult(plan.sql, rows, plan.interpretation, plan.assumptions)

    return build_workflow(
        WorkflowDeps(
            llm_generate=llm.generate,
            llm_model=llm.model_id,
            search_docs=lambda query, top_k, tenant_id: services.retriever.docs(query, top_k, None, tenant_id=tenant_id),
            run_database=run_database,
            answer_generator=services.generator,
            db_configured=bool(settings.DB_ENABLED and settings.DB_URL),
            llm_routing=settings.WORKFLOW_LLM_ROUTING,
        )
    )
