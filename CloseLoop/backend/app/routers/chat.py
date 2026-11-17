"""Chatbot ("Close Bhai") — authenticated front door to the query gateway.

    GET    /api/chat/status               -> {enabled}
    POST   /api/chat/stream               -> Server-Sent Events: status, route, meta, sources, token, done, error
    DELETE /api/chat/session/{session_id} -> forget one conversation

    GET    /api/chat/documents                 -> the organization's uploaded documents
    POST   /api/chat/documents                 -> upload files (multipart `files`); returns a batch id
    GET    /api/chat/documents/batches/{id}    -> processing progress of one upload
    DELETE /api/chat/documents/{document_id}   -> remove one (uploader or Super Admin)

Tenant isolation lives here, not in the browser and not in the prompt:

  * the tenant is ALWAYS the signed-in user's own `tenant_id`, read from the database row behind
    their session cookie. The request body has no tenant field, and any extra field a caller adds
    (e.g. a `tenant_id` meant to peek at another company) is ignored;
  * every signed-in user of a tenant, whatever their role, can ask about that tenant's data;
  * the Platform Owner has no tenant, so has no data to ask about and is refused;
  * the shared gateway keys stay in the server environment;
  * uploaded documents follow the same rule: the ingestion API is given the user's own tenant and
    only ever lists, searches or deletes that tenant's documents (plus the shared product docs,
    which are read-only here).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Role, Tenant, TenantStatus, User
from ..services import chatbot_documents, chatbot_service, report_chat, report_intent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
BATCH_ID_PATTERN = r"^[0-9a-f]{32}$"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Random id the browser keeps for one chat; ties follow-up questions together. Scoped to the
    # user on the server, so it cannot be used to read someone else's conversation.
    session_id: Optional[str] = Field(default=None, pattern=SESSION_ID_PATTERN)


class ChatStatus(BaseModel):
    enabled: bool
    documents: bool = False  # can this user upload documents for the assistant to read?


async def _active_tenant_id(me: User, db: AsyncSession) -> str:
    """The tenant this user may query: their own, and only while that tenant is active."""
    if not me.tenant_id:
        raise HTTPException(403, "The assistant is only available to users of a tenant")
    status = (
        await db.execute(select(Tenant.status).where(Tenant.id == me.tenant_id))
    ).scalar_one_or_none()
    if status != TenantStatus.active:
        raise HTTPException(403, "Your organization is not active")
    return me.tenant_id


@router.get("/status", response_model=ChatStatus)
async def chat_status(me: User = Depends(get_current_user)):
    enabled = bool(me.tenant_id) and chatbot_service.is_configured()
    return ChatStatus(enabled=enabled, documents=enabled and chatbot_documents.is_configured())


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Question is required")
    tenant_id = await _active_tenant_id(me, db)

    # "Generate the monthly report" asks the app to PRODUCE something; it is answered here with the same
    # report engine and permissions as the Report Studio page, not sent to the language-model gateway.
    if report_intent.looks_like_report_request(question):
        try:
            chatbot_service.acquire(me.id)
        except chatbot_service.ChatbotBusy as exc:
            raise HTTPException(
                429, "You are asking too quickly. Please wait a moment and try again.",
                headers={"Retry-After": str(exc.retry_after)},
            )
        try:
            frames = await report_chat.reply_frames(db, me, question)
        finally:
            chatbot_service.release(me.id)
        logger.info("chat report request user=%s tenant=%s chars=%d", me.id, tenant_id, len(question))
        return StreamingResponse(
            iter(frames),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    try:
        upstream = await chatbot_service.open_stream(
            question=question,
            tenant_id=tenant_id,
            user_id=me.id,
            client_session_id=body.session_id,
        )
    except chatbot_service.ChatbotNotConfigured:
        raise HTTPException(503, "The assistant is not available")
    except chatbot_service.ChatbotBusy as exc:
        raise HTTPException(
            429,
            "You are asking too quickly. Please wait a moment and try again.",
            headers={"Retry-After": str(exc.retry_after)},
        )
    except chatbot_service.ChatbotUnavailable:
        raise HTTPException(502, "The assistant is unavailable right now. Please try again shortly.")

    logger.info(
        "chat question user=%s tenant=%s chars=%d session=%s",
        me.id, tenant_id, len(question), "yes" if body.session_id else "no",
    )
    return StreamingResponse(
        chatbot_service.relay(upstream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # tell nginx hops not to buffer the stream
        },
        background=BackgroundTask(upstream.aclose),  # backstop; relay() also closes it
    )


@router.delete("/session/{session_id}", status_code=204)
async def chat_clear_session(
    session_id: str,
    me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(422, "Invalid session id")
    tenant_id = await _active_tenant_id(me, db)
    await chatbot_service.clear_session(
        tenant_id=tenant_id, user_id=me.id, client_session_id=session_id
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Documents the assistant can read (uploaded by the organization's own users)
# ---------------------------------------------------------------------------
_REJECTED = {
    404: "Document not found",
    413: "A file is too large",
    415: "This file type is not supported",
}


@contextmanager
def _ingest_errors():
    """Ingestion failures -> HTTP errors with fixed wording (upstream text is never relayed)."""
    try:
        yield
    except chatbot_documents.IngestNotConfigured:
        raise HTTPException(503, "Document upload is not available")
    except chatbot_documents.IngestRejected as exc:
        raise HTTPException(exc.status, _REJECTED.get(exc.status, "Request refused"))
    except chatbot_documents.IngestUnavailable:
        raise HTTPException(502, "The document service is unavailable right now. Please try again shortly.")


@router.get("/documents")
async def list_documents(me: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tenant_id = await _active_tenant_id(me, db)
    with _ingest_errors():
        docs = await chatbot_documents.list_documents(tenant_id, me.id)
    return {"documents": docs, "limit": settings.CHATBOT_MAX_DOCUMENTS}


@router.post("/documents", status_code=202)
async def upload_documents(
    files: List[UploadFile] = File(...),
    me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await _active_tenant_id(me, db)
    if not chatbot_documents.is_configured():
        raise HTTPException(503, "Document upload is not available")
    if not files or any(not (f.filename or "").strip() for f in files):
        raise HTTPException(422, "Choose at least one file")
    if len(files) > settings.CHATBOT_MAX_FILES_PER_UPLOAD:
        raise HTTPException(413, f"Upload at most {settings.CHATBOT_MAX_FILES_PER_UPLOAD} files at a time")

    with _ingest_errors():
        existing = await chatbot_documents.list_documents(tenant_id)
        # Uploading a file with the same name replaces it, so only new names count against the limit.
        new_names = {f.filename for f in files} - {d["name"] for d in existing}
        if len(existing) + len(new_names) > settings.CHATBOT_MAX_DOCUMENTS:
            raise HTTPException(
                409,
                f"Your organization can keep up to {settings.CHATBOT_MAX_DOCUMENTS} documents. "
                "Delete some to add more.",
            )
        result = await chatbot_documents.upload(
            tenant_id, me.id, [(f.filename, f.file, f.content_type or "") for f in files]
        )
    logger.info(
        "chat documents upload user=%s tenant=%s files=%d accepted=%d",
        me.id, tenant_id, len(files), result["accepted"],
    )
    return result


@router.get("/documents/batches/{batch_id}")
async def upload_progress(
    batch_id: str,
    me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not re.fullmatch(BATCH_ID_PATTERN, batch_id):
        raise HTTPException(422, "Invalid batch id")
    tenant_id = await _active_tenant_id(me, db)
    with _ingest_errors():
        return await chatbot_documents.batch_status(tenant_id, batch_id)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await _active_tenant_id(me, db)
    if not chatbot_documents.owns(tenant_id, document_id):
        raise HTTPException(404, "Document not found")
    with _ingest_errors():
        uploaded_by = await chatbot_documents.uploader_of(tenant_id, document_id)  # 404 if not this tenant's
        if getattr(me, "role", None) != Role.SUPER_ADMIN and not (uploaded_by and uploaded_by == me.id):
            raise HTTPException(403, "Only the person who uploaded a document, or an admin, can delete it")
        await chatbot_documents.delete_document(tenant_id, document_id)
    logger.info("chat documents delete user=%s tenant=%s", me.id, tenant_id)
    return Response(status_code=204)
