"""Trusted bridge between CloseLoop users and the chatbot's ingestion API (document upload).

Same rules as `chatbot_service` (the question side):

  * the tenant is the logged-in user's own `tenant_id`, passed in by the router from the database;
    it is sent to the ingestion API as `X-Tenant-Id`, which scopes every upload, job and document to
    it. A user can therefore only ever add, list or delete documents of their own tenant;
  * the shared key comes from the server environment and never reaches the browser;
  * what is returned to the browser is whitelisted, and upstream error text is never relayed.

The ingestion API stores a tenant's document under the id `<tenant_id>__<file name>`; the tenant
prefix is removed again before anything is shown.
"""

from __future__ import annotations

import logging
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# (file name, open file, content type)
UploadItem = Tuple[str, BinaryIO, str]


class IngestNotConfigured(Exception):
    """Chatbot or document upload is off, or the URL / key is missing."""


class IngestUnavailable(Exception):
    """The ingestion API is unreachable or answered with an unexpected status."""


class IngestRejected(Exception):
    """The ingestion API refused this request in a way the user can act on (`status` is HTTP)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"rejected {status}")
        self.status = status


def is_configured() -> bool:
    return bool(
        settings.CHATBOT_ENABLED
        and settings.CHATBOT_INGEST_ENABLED
        and settings.CHATBOT_INGEST_URL.strip()
        and settings.CHATBOT_INGEST_API_KEY
    )


def document_id_prefix(tenant_id: str) -> str:
    return f"{tenant_id}__"


def owns(tenant_id: str, document_id: str) -> bool:
    """Does this stored document id belong to the tenant? (The ingestion API checks too.)"""
    return document_id.startswith(document_id_prefix(tenant_id)) and len(document_id) > len(
        document_id_prefix(tenant_id)
    )


def display_name(tenant_id: str, document_id: str) -> str:
    return document_id.removeprefix(document_id_prefix(tenant_id))


def _headers(tenant_id: str, user_id: Optional[str] = None) -> Dict[str, str]:
    headers = {"X-API-Key": settings.CHATBOT_INGEST_API_KEY, "X-Tenant-Id": tenant_id}
    if user_id:
        headers["X-User-Id"] = user_id
    return headers


async def _request(
    method: str,
    path: str,
    tenant_id: str,
    *,
    user_id: Optional[str] = None,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """One call to the ingestion API. Returns parsed JSON (or None for an empty body).

    Raises IngestNotConfigured / IngestRejected (400-class answers the user can act on: 404, 413, 415)
    / IngestUnavailable (anything else)."""
    if not is_configured():
        raise IngestNotConfigured()
    if not tenant_id:
        raise IngestNotConfigured("tenant_id is required")
    try:
        async with httpx.AsyncClient(
            base_url=settings.CHATBOT_INGEST_URL.rstrip("/"),
            timeout=httpx.Timeout(timeout or 15.0, connect=5.0),
        ) as client:
            response = await client.request(method, path, headers=_headers(tenant_id, user_id), **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("chatbot ingestion unreachable: %s", type(exc).__name__)
        raise IngestUnavailable("ingestion unreachable") from exc

    status = response.status_code
    if status in (404, 413, 415):
        raise IngestRejected(status)
    if status >= 400:
        logger.warning("chatbot ingestion answered %s for %s %s", status, method, path)
        raise IngestUnavailable(f"ingestion returned {status}")
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise IngestUnavailable("ingestion returned invalid JSON") from exc


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _shape_document(tenant_id: str, row: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    doc_id = str(row.get("document_id", ""))
    uploaded_by = row.get("uploaded_by")
    return {
        "id": doc_id,
        "name": display_name(tenant_id, doc_id),
        "format": row.get("file_format"),
        "chunks": int(row.get("chunk_count") or 0),
        "mine": bool(user_id and uploaded_by and uploaded_by == user_id),
    }


async def list_documents(tenant_id: str, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    data = await _request("GET", "/documents", tenant_id)
    rows = data.get("documents", []) if isinstance(data, dict) else []
    docs = [
        _shape_document(tenant_id, r, user_id)
        for r in rows
        if isinstance(r, dict) and owns(tenant_id, str(r.get("document_id", "")))
    ]
    return sorted(docs, key=lambda d: d["name"].lower())


async def uploader_of(tenant_id: str, document_id: str) -> Optional[str]:
    """Who uploaded this document ('' if unknown). Raises IngestRejected(404) if it is not the tenant's."""
    data = await _request(
        "GET", f"/documents/{quote(document_id, safe='')}", tenant_id, params={"include_text": "false"}
    )
    chunks = data.get("chunks", []) if isinstance(data, dict) else []
    return str(chunks[0].get("uploaded_by") or "") if chunks and isinstance(chunks[0], dict) else ""


async def upload(tenant_id: str, user_id: str, files: Sequence[UploadItem]) -> Dict[str, Any]:
    """Queue the files for processing. Returns {batch_id, accepted, rejected: [{name, status}]}."""
    data = await _request(
        "POST",
        "/upload/batch",
        tenant_id,
        user_id=user_id,
        timeout=float(settings.CHATBOT_INGEST_TIMEOUT_SECONDS),
        files=[("files", (name, fh, ctype or "application/octet-stream")) for name, fh, ctype in files],
    )
    data = data if isinstance(data, dict) else {}
    return {
        "batch_id": data.get("batch_id"),
        "accepted": int(data.get("accepted") or 0),
        "rejected": [
            {"name": str(r.get("filename") or ""), "status": int(r.get("status") or 0)}
            for r in data.get("rejected", [])
            if isinstance(r, dict)
        ],
    }


async def batch_status(tenant_id: str, batch_id: str) -> Dict[str, Any]:
    data = await _request("GET", f"/batches/{batch_id}", tenant_id, params={"include_jobs": "true"})
    data = data if isinstance(data, dict) else {}
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    return {
        "total": int(data.get("total") or 0),
        "finished": bool(data.get("finished")),
        "done": int(counts.get("done") or 0),
        "failed": int(counts.get("failed") or 0),
        "failures": [
            {"name": str(j.get("filename") or ""), "reason": _failure_reason(str(j.get("error") or ""))}
            for j in data.get("jobs", [])
            if isinstance(j, dict) and j.get("status") == "failed"
        ],
    }


def _failure_reason(error: str) -> str:
    """A short, safe sentence for the user; raw worker errors stay in the server logs."""
    low = error.lower()
    if "no tenant" in low:
        return "Could not be processed"
    if "unsupported" in low or "support needs" in low:
        return "This file type is not supported"
    return "The file could not be read"


async def delete_document(tenant_id: str, document_id: str) -> None:
    await _request("DELETE", f"/documents/{quote(document_id, safe='')}", tenant_id)
