"""Ingestion job state, kept in a dedicated Redis (persistent, no eviction) so uploads can be tracked.

Keys: ing:job:<job_id> (hash), ing:batch:<batch_id> (set of job ids). Both expire after JOB_TTL_SECONDS.
Every job remembers the tenant that uploaded it; lookups made for another tenant find nothing.
"""
import os
import time
from functools import lru_cache

import redis

JOB_TTL_SECONDS = int(os.environ.get("INGEST_JOB_TTL_SECONDS", 7 * 24 * 3600))
STATUSES = ("queued", "processing", "retrying", "done", "failed")


@lru_cache
def _r() -> redis.Redis:
    return redis.Redis.from_url(os.environ.get("JOBS_REDIS_URL", "redis://localhost:6379/1"), decode_responses=True)


def create_job(
    job_id: str, filename: str, batch_id: str | None = None, tenant_id: str = "", uploaded_by: str = ""
) -> None:
    now = str(time.time())
    p = _r().pipeline()
    p.hset(f"ing:job:{job_id}", mapping={
        "job_id": job_id, "filename": filename, "status": "queued", "batch_id": batch_id or "",
        "tenant_id": tenant_id, "uploaded_by": uploaded_by,
        "chunks": 0, "attempts": 0, "error": "", "created_at": now, "updated_at": now,
    })
    p.expire(f"ing:job:{job_id}", JOB_TTL_SECONDS)
    if batch_id:
        p.sadd(f"ing:batch:{batch_id}", job_id)
        p.expire(f"ing:batch:{batch_id}", JOB_TTL_SECONDS)
    p.execute()


def update_job(job_id: str, **fields) -> None:
    fields["updated_at"] = str(time.time())
    _r().hset(f"ing:job:{job_id}", mapping={k: str(v) for k, v in fields.items()})


def get_job(job_id: str, tenant_id: str | None = None) -> dict | None:
    """The job, or None if it does not exist or (when ``tenant_id`` is given) belongs to another tenant."""
    data = _r().hgetall(f"ing:job:{job_id}")
    if not data or (tenant_id is not None and data.get("tenant_id") != tenant_id):
        return None
    return _shape(data)


def _shape(d: dict) -> dict:
    d["chunks"], d["attempts"] = int(d.get("chunks", 0)), int(d.get("attempts", 0))
    d["created_at"], d["updated_at"] = float(d["created_at"]), float(d["updated_at"])
    return d


def batch_summary(batch_id: str, include_jobs: bool = False, tenant_id: str | None = None) -> dict | None:
    ids = sorted(_r().smembers(f"ing:batch:{batch_id}"))
    if not ids:
        return None
    p = _r().pipeline()
    for i in ids:
        p.hgetall(f"ing:job:{i}")
    jobs = [_shape(j) for j in p.execute() if j and (tenant_id is None or j.get("tenant_id") == tenant_id)]
    if not jobs:
        return None
    ids = [j["job_id"] for j in jobs]
    counts = {s: 0 for s in STATUSES}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    finished = counts["done"] + counts["failed"]
    out = {"batch_id": batch_id, "total": len(ids), "counts": counts, "finished": finished == len(ids)}
    if include_jobs:
        out["jobs"] = [{k: j[k] for k in ("job_id", "filename", "status", "chunks", "attempts", "error")} for j in jobs]
    return out


def ping() -> bool:
    try:
        return bool(_r().ping())
    except Exception:
        return False
