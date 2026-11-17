"""Celery app + the document-processing task. Run the worker from this folder:

    celery -A tasks worker -Q ingestion -c 4

The API only saves the file and enqueues; this worker does parse -> chunk -> embed -> Qdrant.
RabbitMQ keeps the queue durable, tasks are acked after they finish (acks_late), so a crashed worker
hands its file back to the queue instead of losing it.
"""
import os
import shutil
from pathlib import Path

import requests
from celery import Celery
from loguru import logger

import jobs

celery_app = Celery("ingestion", broker=os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//"))
celery_app.conf.update(
    task_default_queue="ingestion",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # one file per worker slot at a time: fair, and no 1000 files stuck in one prefetch
    broker_connection_retry_on_startup=True,
    task_time_limit=int(os.environ.get("INGEST_TASK_TIME_LIMIT", 900)),
    task_annotations={"ingestion.process_document": {"rate_limit": os.environ.get("INGEST_RATE_LIMIT", "120/m")}},
)

MAX_RETRIES = int(os.environ.get("INGEST_MAX_RETRIES", 6))
_orchestrator = None


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from main import DataLoadOrchestrator

        _orchestrator = DataLoadOrchestrator()
    return _orchestrator


def _retryable(error: Exception) -> bool:
    """Rate limits, 5xx and network/storage outages are worth retrying; bad files are not."""
    if isinstance(error, requests.HTTPError):
        code = error.response.status_code if error.response is not None else 0
        return code == 429 or code >= 500
    if isinstance(error, (requests.ConnectionError, requests.Timeout, ConnectionError, TimeoutError)):
        return True
    return type(error).__module__.startswith("qdrant_client") or type(error).__name__ == "ResponseHandlingException"


def _cleanup(path: str) -> None:
    shutil.rmtree(Path(path).parent, ignore_errors=True)  # uploads live in one folder per job


@celery_app.task(bind=True, name="ingestion.process_document", max_retries=MAX_RETRIES)
def process_document(self, job_id: str, path: str) -> dict:
    attempt = self.request.retries + 1
    job = jobs.get_job(job_id) or {}
    if not job.get("tenant_id"):
        # Fail closed: a file with no owner must never be indexed where every tenant can read it.
        jobs.update_job(job_id, status="failed", error="Job has no tenant")
        logger.error(f"[{job_id}] refused: job has no tenant")
        _cleanup(path)
        return {"job_id": job_id, "status": "failed"}
    jobs.update_job(job_id, status="processing", attempts=attempt)
    try:
        chunks = _get_orchestrator().run_local_file(
            path, strict=True, tenant_id=job["tenant_id"], uploaded_by=job.get("uploaded_by") or None
        )
    except Exception as error:
        if _retryable(error) and self.request.retries < MAX_RETRIES:
            delay = min(2 ** attempt * 5, 300)  # 10s, 20s, 40s ... capped at 5 min
            jobs.update_job(job_id, status="retrying", error=f"{type(error).__name__}: {error}"[:300])
            logger.warning(f"[{job_id}] attempt {attempt} failed ({error}); retrying in {delay}s")
            raise self.retry(exc=error, countdown=delay)
        jobs.update_job(job_id, status="failed", error=f"{type(error).__name__}: {error}"[:300])
        logger.error(f"[{job_id}] failed permanently: {error}")
        _cleanup(path)
        return {"job_id": job_id, "status": "failed"}
    from utils.docs_version import bump_docs_version

    bump_docs_version()  # retire the gateway's cached searches
    jobs.update_job(job_id, status="done", chunks=len(chunks), error="")
    logger.info(f"[{job_id}] done: {len(chunks)} chunks")
    _cleanup(path)
    return {"job_id": job_id, "status": "done", "chunks": len(chunks)}
