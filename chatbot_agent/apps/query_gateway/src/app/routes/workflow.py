"""POST /workflow/ask: one endpoint that routes a question to documents, database, or both."""
import asyncio
import hmac
import json
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from app import memory
from app.deps import get_workflow, require_api_key
from core.config import Settings, get_settings
from core.exceptions import GatewayError
from domain.schemas import WorkflowRequest, WorkflowResponse
from shared.constants.enums import EvidenceSource
from shared.schemas.answer import SourceRef
from workflow.workflow import run_workflow, stream_workflow

router = APIRouter(dependencies=[Depends(require_api_key)])

HEARTBEAT_S = 15  # keep idle connections (proxies, load balancers) from timing out
_END = object()


def database_allowed(settings: Settings, sql_api_key: str | None) -> bool:
    """The database branch needs the same switches and shared key as /sql/ask."""
    return bool(
        settings.DB_ENABLED
        and settings.DB_URL
        and settings.SQL_PLANNER_API_ENABLED
        and settings.SQL_QUERY_EXECUTION_ENABLED
        and settings.SQL_PLANNER_API_KEY
        and sql_api_key
        and hmac.compare_digest(sql_api_key, settings.SQL_PLANNER_API_KEY)
    )


@router.post("/workflow/ask", response_model=WorkflowResponse)
def workflow_ask(
    request: WorkflowRequest,
    x_sql_api_key: str | None = Header(default=None, alias="X-SQL-API-Key"),
    workflow=Depends(get_workflow),
) -> WorkflowResponse:
    """Classify intent, then run document search and/or the database query (in parallel) and answer."""
    question = request.question.strip()
    if not question:
        raise HTTPException(400, "Question is required.")

    history, memory_warnings = memory.load_history(request.tenant_id, request.session_id)
    state = run_workflow(
        workflow,
        question,
        tenant_id=request.tenant_id,
        top_k=request.top_k,
        max_rows=request.max_rows,
        db_allowed=database_allowed(get_settings(), x_sql_api_key),
        history=history,
        warnings=memory_warnings,
    )
    memory.save_turn(request.tenant_id, request.session_id, question, state["answer"])
    sources = [
        SourceRef(
            document_id=item.document_id,
            title=item.title,
            section=item.section,
            score=item.score,
            source=item.source.value,
        )
        for item in state.get("evidence", [])
        if item.source == EvidenceSource.DOCUMENT
    ]
    logger.info(
        "workflow route={} source={} steps={}",
        state["route"],
        state.get("route_source"),
        [(step["node"], step["ms"]) for step in state["trace"]],
    )
    return WorkflowResponse(
        question=question,
        answer=state["answer"],
        route=state["route"],
        route_reason=state.get("route_reason", ""),
        route_source=state.get("route_source", "rules"),
        needs_clarification=bool(state.get("clarification")) and not state.get("evidence"),
        sources=sources,
        sql=state.get("db_sql"),
        row_count=state.get("db_row_count"),
        interpretation=state.get("db_interpretation"),
        assumptions=state.get("db_assumptions", []),
        warnings=state.get("warnings", []),
        trace=state["trace"],
        session_id=request.session_id,
        rewritten_question=_rewritten(state, question),
    )


@router.get("/workflow/graph")
def workflow_graph(workflow=Depends(get_workflow)) -> dict[str, str]:
    """The compiled graph as a Mermaid diagram, for documentation and debugging."""
    return {"mermaid": workflow.get_graph().draw_mermaid()}


# ---- streaming (Server-Sent Events) ------------------------------------------------------------------
def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _rewritten(state: dict[str, Any], question: str) -> str | None:
    standalone = state.get("standalone_question")
    return standalone if standalone and standalone.strip().lower() != question.strip().lower() else None


def _done_payload(state: dict[str, Any], started: float, request: WorkflowRequest, question: str) -> dict[str, Any]:
    return {
        "session_id": request.session_id,
        "rewritten_question": _rewritten(state, question),
        "route": state.get("route"),
        "needs_clarification": bool(state.get("clarification")) and not state.get("evidence"),
        "warnings": state.get("warnings", []),
        "trace": state.get("trace", []),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


async def event_stream(workflow, question: str, request: WorkflowRequest, db_allowed: bool):
    """Run the graph in a worker thread and relay its events as they happen.

    Events: status, route, meta, sources, token, done, error. A client disconnect cancels this
    generator; the ``finally`` then sets ``cancel`` so the worker stops reading the model's stream
    (which closes the vendor connection and stops generation).
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()
    started = time.perf_counter()

    def put(item: tuple[Any, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        try:
            history, memory_warnings = memory.load_history(request.tenant_id, request.session_id)
            logger.debug(
                "workflow stream started: session={} history_turns={} question_chars={}",
                request.session_id,
                len(history),
                len(question),
            )
            for kind, payload in stream_workflow(
                workflow,
                question,
                tenant_id=request.tenant_id,
                top_k=request.top_k,
                max_rows=request.max_rows,
                db_allowed=db_allowed,
                cancel=cancel,
                history=history,
                warnings=memory_warnings,
            ):
                if kind == "final" and payload and not cancel.is_set():
                    # Only a finished answer is remembered: never a half-streamed or abandoned one.
                    memory.save_turn(request.tenant_id, request.session_id, question, payload.get("answer", ""))
                put((kind, payload))
        except GatewayError as error:
            put(("error", {
                "message": error.public_message,
                "status": error.status_code,
                "retry_after": getattr(error, "retry_after", 0),
            }))
        except Exception:
            logger.exception("Streaming workflow crashed")
            put(("error", {"message": "Internal server error", "status": 500, "retry_after": 0}))
        finally:
            put((_END, None))

    loop.run_in_executor(None, worker)
    yield ": connected\n\n"  # flush the headers immediately
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if kind is _END:
                break
            if kind == "event":
                data = dict(payload)
                yield sse(data.pop("type", "message"), data)
            elif kind == "final":
                if payload:
                    logger.info(
                        "workflow stream route={} steps={}",
                        payload.get("route"),
                        [(step["node"], step["ms"]) for step in payload.get("trace", [])],
                    )
                    yield sse("done", _done_payload(payload, started, request, question))
            elif kind == "error":
                yield sse("error", payload)
    finally:
        cancel.set()


@router.post("/workflow/stream")
async def workflow_stream(
    request: WorkflowRequest,
    x_sql_api_key: str | None = Header(default=None, alias="X-SQL-API-Key"),
    workflow=Depends(get_workflow),
) -> StreamingResponse:
    """Same routing as /workflow/ask, but streamed as Server-Sent Events: progress, then the answer."""
    question = request.question.strip()
    if not question:
        raise HTTPException(400, "Question is required.")
    return StreamingResponse(
        event_stream(workflow, question, request, database_allowed(get_settings(), x_sql_api_key)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.delete("/workflow/session/{session_id}", status_code=204)
def delete_session(session_id: str, tenant_id: str | None = None) -> None:
    """Forget one conversation ("new chat" / "delete my data"). Safe to call twice."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", session_id):
        raise HTTPException(422, "Invalid session id.")
    memory.clear_session(tenant_id, session_id)
