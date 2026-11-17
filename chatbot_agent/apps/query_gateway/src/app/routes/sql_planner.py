"""Authenticated SQL planning and read-only tenant-query API."""
import hmac
import psycopg

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from core.config import get_settings
from llm.client import LLMClient
from core.exceptions import FeatureDisabled, LLMUnavailable
from db import executor
from db.sql_planner import (
    SQLPlan,
    MAX_ROWS,
    ClarificationNeeded,
    SQLPlanningError,
    plan_sql,
    plan_sql_detailed,
)

router = APIRouter()


class SQLPlanRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant_id: str = Field(min_length=1, max_length=128)
    max_rows: int = Field(default=50, ge=1, le=MAX_ROWS)


class SQLQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    tenant_id: str = Field(min_length=1, max_length=128)
    max_rows: int = Field(default=50, ge=1, le=MAX_ROWS)


class SQLQueryResponse(BaseModel):
    question: str
    answer: str
    sql: str | None = None
    row_count: int = 0
    result_data_truncated: bool = False
    interpretation: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False


def _llm_http_error(error: LLMUnavailable) -> HTTPException:
    retry_after = getattr(error, "retry_after", 0)
    return HTTPException(
        status_code=error.status_code,
        detail=error.public_message,
        headers={"Retry-After": str(retry_after)} if retry_after else None,
    )


def require_sql_api_key(
    x_sql_api_key: str | None = Header(default=None, alias="X-SQL-API-Key"),
) -> None:
    settings = get_settings()
    expected = settings.SQL_PLANNER_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="SQL_PLANNER_API_KEY is not configured.")
    if not x_sql_api_key or not hmac.compare_digest(x_sql_api_key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid X-SQL-API-Key header.")
    if not settings.SQL_PLANNER_API_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")
    if not settings.DB_ENABLED or not settings.DB_URL:
        raise HTTPException(status_code=503, detail="Database planning is not configured.")
    return settings


@router.post(
    "/sql/plan",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_sql_api_key)],
    responses={
        200: {"content": {"text/plain": {"schema": {"type": "string"}}}},
        401: {"description": "Missing or invalid API key."},
        404: {"description": "SQL planning API is disabled."},
        422: {"description": "Question rejected or generated SQL failed validation."},
        502: {"description": "Gemini is unavailable."},
        503: {"description": "Database planning is not configured."},
    },
)
def generate_sql(
    request: SQLPlanRequest,
) -> PlainTextResponse:
    """Return pure validated SQL for exactly the requested tenant; do not execute it."""
    try:
        sql = plan_sql(
            request.question,
            tenant_id=request.tenant_id,
            max_rows=request.max_rows,
        )
    except SQLPlanningError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except FeatureDisabled as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except LLMUnavailable as error:
        raise _llm_http_error(error) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="The database is unavailable.",
        ) from error
    return PlainTextResponse(sql, media_type="text/plain")


@router.post(
    "/sql/ask",
    response_model=SQLQueryResponse,
    dependencies=[Depends(require_sql_api_key)],
    responses={
        401: {"description": "Missing or invalid API key."},
        404: {"description": "SQL planning or execution API is disabled."},
        422: {"description": "Question rejected or generated SQL failed validation."},
        502: {"description": "Gemini is unavailable."},
        503: {"description": "Database is unavailable or query execution is not configured."},
    },
)
def ask_database(request: SQLQueryRequest) -> SQLQueryResponse:
    """Guard, plan and run a read-only tenant query, then answer in natural language."""
    settings = get_settings()
    if not settings.SQL_QUERY_EXECUTION_ENABLED:
        raise HTTPException(status_code=404, detail="Database query execution is disabled.")

    client = LLMClient(settings)
    try:
        plan = plan_sql_detailed(
            request.question,
            tenant_id=request.tenant_id,
            max_rows=request.max_rows,
            client=client,
        )
        rows = executor.execute(
            plan.sql,
            tenant_id=request.tenant_id,
            max_rows=request.max_rows,
        )
        answer, data_truncated = executor.generate_answer(
            request.question,
            rows,
            client,
            context={"interpretation": plan.interpretation, "assumptions": plan.assumptions},
        )
    except ClarificationNeeded as clarification:
        return SQLQueryResponse(
            question=request.question,
            answer=clarification.question,
            needs_clarification=True,
        )
    except SQLPlanningError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except FeatureDisabled as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except LLMUnavailable as error:
        raise _llm_http_error(error) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="The database query could not be completed.",
        ) from error

    return SQLQueryResponse(
        question=request.question,
        answer=answer,
        sql=plan.sql,
        row_count=len(rows),
        result_data_truncated=data_truncated,
        interpretation=plan.interpretation or None,
        assumptions=plan.assumptions,
    )
