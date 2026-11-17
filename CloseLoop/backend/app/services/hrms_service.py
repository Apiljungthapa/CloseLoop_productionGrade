"""Read-only client for the HRMS "external" API.

Used by the HRMS Integration page to list the projects and activities (with
their sub activities) a user can log against. Authentication is a per-tenant HMAC: the `x-tenant-id` header is
`<tenant_id>.<hex HMAC-SHA256(secret, tenant_id)>`. The shared secret comes
from settings (environment) only and is never logged or returned.

The upstream call is blocking (`requests`), so callers run it in a thread
pool. Results are cached briefly so opening the dropdown repeatedly doesn't
hammer the HRMS.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from typing import Any, Optional

import requests

from ..config import settings

logger = logging.getLogger(__name__)


class HrmsNotConfigured(Exception):
    """HRMS_TENANT_ID / HRMS_SECRET are not set."""


class HrmsError(Exception):
    """The HRMS could not be reached or returned something unusable."""


_lock = threading.Lock()
# endpoint name -> (expires_at, normalized list)
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def generate_signature(tenant_id: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), tenant_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def normalize_projects(data: Any) -> list[dict[str, Any]]:
    """HRMS payload -> [{id, name}], de-duplicated by id and sorted by name.
    Accepts the bare list, or a dict wrapping it under "projects"."""
    if isinstance(data, dict):
        data = data.get("projects")
    if not isinstance(data, list):
        raise HrmsError("Unexpected response format from the HRMS")
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pid = item.get("id")
        name = str(item.get("name") or "").strip()
        if pid is None or not name or pid in seen:
            continue
        seen.add(pid)
        out.append({"id": pid, "name": name})
    out.sort(key=lambda p: p["name"].lower())
    return out


def normalize_activities(data: Any) -> list[dict[str, Any]]:
    """HRMS payload -> [{id, name, description, sub_activities: [{id, name, key,
    description, data_type}]}], de-duplicated by id. Sub activities are extra
    fields of the activity: the HRMS wants a value for each. The HRMS order is kept on purpose — sub
    activities are often numbered ("First", "Second", …), which an
    alphabetical sort would scramble."""
    if isinstance(data, dict):
        data = data.get("activities")
    if not isinstance(data, list):
        raise HrmsError("Unexpected response format from the HRMS")
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        aid = item.get("id")
        name = str(item.get("name") or "").strip()
        if aid is None or not name or aid in seen:
            continue
        seen.add(aid)
        subs: list[dict[str, Any]] = []
        sub_seen: set[Any] = set()
        for sub in item.get("subActivities") or []:
            if not isinstance(sub, dict):
                continue
            sid = sub.get("id")
            sname = str(sub.get("name") or "").strip()
            if sid is None or not sname or sid in sub_seen:
                continue
            sub_seen.add(sid)
            key, sdesc, dtype = sub.get("key"), sub.get("description"), sub.get("dataType")
            subs.append({
                "id": sid, "name": sname, "key": str(key) if key else None,
                "description": str(sdesc) if sdesc else None,
                "data_type": dtype if isinstance(dtype, int) else None,
            })
        desc = item.get("description")
        out.append({
            "id": aid,
            "name": name,
            "description": str(desc) if desc else None,
            "sub_activities": subs,
        })
    return out


def normalize_project_members(data: Any) -> list[dict[str, Any]]:
    """HRMS payload -> [{id, name, members: [{id, name, work_email}]}].

    Only what the timesheet needs to tell which HRMS employee a CloseLoop user
    is (id + work e-mail, compared exactly): the rest of each member record -
    birth date, phone, address, ... - is dropped here and never cached."""
    if isinstance(data, dict):
        data = data.get("projects")
    if not isinstance(data, list):
        raise HrmsError("Unexpected response format from the HRMS")
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        members: list[dict[str, Any]] = []
        for m in item.get("members") or []:
            if not isinstance(m, dict) or m.get("id") is None:
                continue
            email = str(m.get("workEmail") or "").strip().lower()
            if not email:
                continue
            name = " ".join(
                str(m.get(k)).strip() for k in ("firstName", "middleName", "lastName") if m.get(k)
            )
            members.append({"id": m["id"], "name": name, "work_email": email})
        out.append({"id": item["id"], "name": str(item.get("name") or "").strip(), "members": members})
    return out


def signed_headers() -> dict[str, str]:
    """The per-tenant auth header every HRMS "external" call carries. Raises
    HrmsNotConfigured when the tenant id / secret aren't set."""
    tenant_id = (settings.HRMS_TENANT_ID or "").strip()
    secret = (settings.HRMS_SECRET or "").strip()
    if not tenant_id or not secret:
        raise HrmsNotConfigured()
    return {
        "x-tenant-id": f"{tenant_id}.{generate_signature(tenant_id, secret)}",
        "Accept": "application/json",
    }


def _fetch(endpoint: str, normalize) -> list[dict[str, Any]]:
    headers = signed_headers()
    url = f"{settings.HRMS_BASE_URL.rstrip('/')}/v1/external/{endpoint}"
    try:
        resp = requests.get(url, headers=headers, timeout=settings.HRMS_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("HRMS %s request failed (%s)", endpoint, type(exc).__name__)
        raise HrmsError("Could not reach the HRMS") from exc

    try:
        body = resp.json()
    except ValueError:
        body = None
    if not resp.ok or not isinstance(body, dict) or "data" not in body:
        logger.warning("HRMS %s request returned HTTP %s", endpoint, resp.status_code)
        raise HrmsError(f"The HRMS returned an error (HTTP {resp.status_code})")
    return normalize(body["data"])


def _cached(
    endpoint: str, normalize, now: Optional[float], cache_key: Optional[str] = None
) -> list[dict[str, Any]]:
    key = cache_key or endpoint
    t = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > t:
            return list(hit[1])
    rows = _fetch(endpoint, normalize)
    with _lock:
        _cache[key] = (t + max(0, settings.HRMS_CACHE_SECONDS), rows)
    return list(rows)


def get_projects(*, now: Optional[float] = None) -> list[dict[str, Any]]:
    """Cached project list. Raises HrmsNotConfigured / HrmsError."""
    return _cached("projects", normalize_projects, now)


def get_activities(*, now: Optional[float] = None) -> list[dict[str, Any]]:
    """Cached activity list (with sub activities). Raises HrmsNotConfigured /
    HrmsError."""
    return _cached("activities", normalize_activities, now)


def get_project_members(project_id: int, *, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """{id, name, members: [{id, name, work_email}]} for one HRMS project, or
    None when the HRMS has no such project. Raises HrmsNotConfigured / HrmsError."""
    rows = _cached("projects", normalize_project_members, now, cache_key="projects:members")
    return next((p for p in rows if p["id"] == project_id), None)


def get_member_emails(*, now: Optional[float] = None) -> set[str]:
    """Work e-mails of everyone who is a member of any HRMS project - i.e. the
    people who can have a timesheet. Raises HrmsNotConfigured / HrmsError."""
    rows = _cached("projects", normalize_project_members, now, cache_key="projects:members")
    return {m["work_email"] for p in rows for m in p["members"]}


def find_employee(project: dict[str, Any], email: str) -> Optional[dict[str, Any]]:
    """The project member whose work e-mail equals `email` exactly (case
    ignored). Never a fuzzy or partial match - a wrong employee would put
    hours on somebody else's timesheet."""
    wanted = (email or "").strip().lower()
    if not wanted:
        return None
    return next((m for m in project.get("members", []) if m["work_email"] == wanted), None)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
