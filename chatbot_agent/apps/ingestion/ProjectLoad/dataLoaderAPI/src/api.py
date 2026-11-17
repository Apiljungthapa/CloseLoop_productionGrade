"""Ingestion API: upload -> clean -> chunk -> embed -> Qdrant, plus document management.
Questions are answered by the query gateway (apps/query_gateway), not here.

Run from the dataLoaderAPI folder:
    uvicorn api:app --app-dir apps/ingestion/ProjectLoad/dataLoaderAPI/src --port 8001
"""
import hmac
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

import jobs
from config import batch_config
from data_loader.local_loader import EXTENSION_FORMATS
from main import DataLoadOrchestrator
from tasks import process_document
from utils.docs_version import bump_docs_version
from vector_store.qdrant_store import SHARED_TENANT


PUBLIC_PATHS = {"/health", "/ready"}  # probes (the container health check sends no key)


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Enforced only when API_AUTH_KEY is configured."""
    expected = batch_config.API_AUTH_KEY
    if request.url.path in PUBLIC_PATHS:
        return
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "Missing or invalid X-API-Key header.")


TENANT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def require_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    """The company a request acts for. Sent by the CloseLoop backend from the signed-in user's own
    record, never by a browser. Every upload, job and document call is limited to this tenant."""
    if not x_tenant_id or not TENANT_PATTERN.fullmatch(x_tenant_id) or x_tenant_id == SHARED_TENANT:
        raise HTTPException(400, "A valid X-Tenant-Id header is required.")
    return x_tenant_id


def uploader(x_user_id: str | None = Header(default=None)) -> str:
    """Who uploaded (recorded on the document, so the app can limit who may delete it)."""
    return (x_user_id or "").strip()[:128]


app = FastAPI(title="Ingestion API", version="1.0.0", dependencies=[Depends(require_api_key)])
orchestrator = DataLoadOrchestrator()

origins = [o.strip() for o in batch_config.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(f"[{request_id}] {request.method} {request.url.path} crashed")
        return JSONResponse({"detail": "Internal server error", "request_id": request_id}, 500)
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(f"[{request_id}] {request.method} {request.url.path} -> {response.status_code} ({elapsed:.0f} ms)")
    return response


# ---- health ----------------------------------------------------------
@app.get("/health", dependencies=[])
def health() -> dict:
    """Liveness probe (no auth)."""
    return {"status": "ok"}


@app.get("/ready", dependencies=[])
def ready() -> JSONResponse:
    """Readiness probe: checks the vector store is reachable."""
    ok = orchestrator.qdrant_store.is_healthy()
    queue = jobs.ping()
    return JSONResponse(
        {"status": "ready" if ok and queue else "unavailable", "vector_store": ok, "job_store": queue},
        200 if ok and queue else 503,
    )


# ---- ingestion -------------------------------------------------------
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))


def _save_upload(file: UploadFile, job_id: str) -> Path:
    """Stream the upload to the shared uploads volume (one folder per job). Raises HTTPException."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in EXTENSION_FORMATS:
        raise HTTPException(415, f"Unsupported extension '{suffix}'. Supported: {sorted(EXTENSION_FORMATS)}")
    folder = UPLOAD_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / (Path(file.filename or "upload.txt").name)
    max_bytes = batch_config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    try:
        with saved.open("wb") as out:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise HTTPException(413, f"File exceeds {batch_config.MAX_UPLOAD_MB} MB limit.")
                out.write(block)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty.")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return saved


def _enqueue(file: UploadFile, batch_id: str | None, tenant_id: str, uploaded_by: str) -> str:
    job_id = uuid.uuid4().hex
    saved = _save_upload(file, job_id)
    jobs.create_job(job_id, file.filename or saved.name, batch_id, tenant_id, uploaded_by)
    try:
        process_document.delay(job_id, str(saved))
    except Exception as error:  # broker down: don't leave an orphan job/file behind
        shutil.rmtree(saved.parent, ignore_errors=True)
        jobs.update_job(job_id, status="failed", error="Queue unavailable")
        logger.error(f"Could not enqueue {file.filename}: {error}")
        raise HTTPException(503, "Processing queue is unavailable. Try again shortly.")
    return job_id


@app.post("/upload", status_code=202)
def upload(
    file: UploadFile = File(...),
    wait: bool = Query(False, description="Process inline and return the chunks"),
    tenant_id: str = Depends(require_tenant),
    uploaded_by: str = Depends(uploader),
) -> dict:
    """Queue one file for processing and return its job id. Poll GET /jobs/{job_id}.

    ?wait=true keeps the old behaviour: process inline and return the chunks.
    """
    if wait:
        with tempfile.TemporaryDirectory() as tmp_dir:
            job_id = uuid.uuid4().hex
            saved = _save_upload(file, job_id)
            try:
                chunks = orchestrator.run_local_file(str(saved), tenant_id=tenant_id, uploaded_by=uploaded_by or None)
            except Exception as error:
                logger.exception(f"Processing failed for {file.filename}")
                raise HTTPException(422, f"Processing failed: {error}")
            finally:
                shutil.rmtree(saved.parent, ignore_errors=True)
        bump_docs_version()
        return {"filename": file.filename, "total_chunks": len(chunks), "chunks": chunks}
    job_id = _enqueue(file, None, tenant_id, uploaded_by)
    return {"job_id": job_id, "filename": file.filename, "status": "queued", "status_url": f"/jobs/{job_id}"}


@app.post("/upload/batch", status_code=202)
def upload_batch(
    files: list[UploadFile] = File(...),
    tenant_id: str = Depends(require_tenant),
    uploaded_by: str = Depends(uploader),
) -> dict:
    """Queue many files at once (up to 1000 per request). Track progress with GET /batches/{batch_id}."""
    batch_id = uuid.uuid4().hex
    accepted, rejected = [], []
    for f in files:
        try:
            accepted.append({"filename": f.filename, "job_id": _enqueue(f, batch_id, tenant_id, uploaded_by)})
        except HTTPException as error:
            if error.status_code == 503:
                raise
            rejected.append({"filename": f.filename, "status": error.status_code, "reason": error.detail})
    return {
        "batch_id": batch_id, "accepted": len(accepted), "rejected": rejected, "jobs": accepted,
        "status_url": f"/batches/{batch_id}",
    }


@app.get("/jobs/{job_id}")
def job_status(job_id: str, tenant_id: str = Depends(require_tenant)) -> dict:
    job = jobs.get_job(job_id, tenant_id)
    if not job:
        raise HTTPException(404, "Job not found (jobs are kept for 7 days).")
    return job


@app.get("/batches/{batch_id}")
def batch_status(batch_id: str, include_jobs: bool = Query(False), tenant_id: str = Depends(require_tenant)) -> dict:
    summary = jobs.batch_summary(batch_id, include_jobs, tenant_id)
    if not summary:
        raise HTTPException(404, "Batch not found (batches are kept for 7 days).")
    return summary


# ---- data getter -----------------------------------------------------
@app.get("/documents")
def list_documents(tenant_id: str = Depends(require_tenant)) -> dict:
    """List the tenant's stored documents with their chunk counts."""
    docs = orchestrator.qdrant_store.list_documents(tenant_id)
    return {"total_documents": len(docs), "documents": docs}


@app.get("/documents/{document_id}")
def get_document(document_id: str, include_text: bool = Query(True), tenant_id: str = Depends(require_tenant)) -> dict:
    """Return all stored chunks of one of the tenant's documents, in order."""
    chunks = orchestrator.qdrant_store.get_document_chunks(document_id, tenant_id)
    if not chunks:
        raise HTTPException(404, f"Document '{document_id}' not found.")
    if not include_text:
        for chunk in chunks:
            chunk.pop("text", None)
    return {"document_id": document_id, "total_chunks": len(chunks), "chunks": chunks}


@app.delete("/documents/{document_id}")
def delete_document(document_id: str, tenant_id: str = Depends(require_tenant)) -> dict:
    """Remove one of the tenant's documents and all its chunks from the vector store."""
    if not orchestrator.qdrant_store.get_document_chunks(document_id, tenant_id):
        raise HTTPException(404, f"Document '{document_id}' not found.")
    orchestrator.qdrant_store.delete_document(document_id, tenant_id)
    bump_docs_version()
    logger.info(f"Deleted document '{document_id}' (tenant {tenant_id})")
    return {"document_id": document_id, "deleted": True}
