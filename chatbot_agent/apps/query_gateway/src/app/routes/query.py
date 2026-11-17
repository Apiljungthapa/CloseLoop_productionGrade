from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from answer.repair_loop import NOT_FOUND_ANSWER, generate_with_repair
from app.deps import Services, get_services, require_api_key
from domain.schemas import AskRequest, AskResponse, RetrieveRequest, RetrieveResponse
from shared.constants.enums import Route
from shared.schemas.answer import SourceRef

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(request: RetrieveRequest, services: Services = Depends(get_services)) -> RetrieveResponse:
    """Ranked document chunks for a query (no LLM)."""
    query = request.query.strip()
    if not query:
        raise HTTPException(400, "Query text is required.")
    items = services.retriever.docs(query, request.top_k, None)
    return RetrieveResponse(query=query, total_matches=len(items), results=items)


@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest, services: Services = Depends(get_services)) -> AskResponse:
    """Simple RAG: retrieve chunks -> generate answer -> validate."""
    question = request.question.strip()
    if not question:
        raise HTTPException(400, "Question is required.")

    chunks = services.retriever.docs(question, request.top_k, None)
    if chunks:
        answer = generate_with_repair(services.generator, question, chunks)
    else:
        answer = NOT_FOUND_ANSWER  # nothing retrieved: don't let the model guess

    sources = [
        SourceRef(document_id=c.document_id, title=c.title, section=c.section, score=c.score, source=c.source.value)
        for c in chunks
    ]
    return AskResponse(question=question, answer=answer, route=Route.DOC.value, sources=sources)
