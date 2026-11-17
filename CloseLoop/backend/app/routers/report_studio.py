"""Report Studio HTTP layer: ask for a report in plain language, get a dashboard or a file.

    GET  /api/report-studio/catalog     report kinds, formats, and the periods / entities / people the caller can pick
    POST /api/report-studio/interpret   {"text": "..."} -> the report it means (or one clarifying question)
    POST /api/report-studio/ask         interpret + build in one call; returns the dashboard payload
    POST /api/report-studio/generate    {"spec": {...}, "format": "json|pdf|xlsx|csv"} -> dashboard JSON or a file

Everything is gated by ``report_access_service`` (Team Leads and Super Admins; entity-scoped) and filtered by
tenant first. Files are rendered in memory in a worker thread - nothing is written to disk.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..auth import require_tenant
from ..database import get_db
from ..models import User
from ..services import report_access_service as access
from ..services import report_exports as exports
from ..services import report_intent, report_request
from ..services.audit_service import log_action
from ..services.report_studio import CATALOG, KINDS, ReportError, ReportSpec, RichReport, build_report

router = APIRouter(prefix="/api/report-studio", tags=["report-studio"])

FILE_TYPES = {
    "pdf": ("application/pdf", "pdf"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "csv": ("text/csv; charset=utf-8", "csv"),
}


class SpecIn(BaseModel):
    kind: str
    period_id: Optional[str] = Field(default=None, max_length=64)
    compare_period_id: Optional[str] = Field(default=None, max_length=64)
    entity_id: Optional[str] = Field(default=None, max_length=64)
    member_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        return v

    def to_spec(self) -> ReportSpec:
        return ReportSpec(
            kind=self.kind, period_id=self.period_id or None, compare_period_id=self.compare_period_id or None,
            entity_id=self.entity_id or None, member_id=self.member_id or None,
        )


class GenerateIn(BaseModel):
    spec: SpecIn
    format: str = "json"
    table: Optional[str] = Field(default=None, max_length=64)  # csv only: which table (default: the main one)

    @field_validator("format")
    @classmethod
    def _known_format(cls, v: str) -> str:
        v = (v or "json").lower()
        if v not in ("json", "pdf", "xlsx", "csv"):
            raise ValueError("format must be one of: json, pdf, xlsx, csv")
        return v


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------
def _fail(exc: ReportError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _safe_filename(stem: str, ext: str) -> str:
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    return f"{re.sub(r'[^A-Za-z0-9._-]+', '_', ascii_stem).strip('._-') or 'report'}.{ext}"


async def _file_response(report: RichReport, fmt: str, *, me: User, tenant_name: Optional[str], table: Optional[str]) -> Response:
    media, ext = FILE_TYPES[fmt]
    try:
        if fmt == "csv":
            try:
                text = await run_in_threadpool(exports.render_csv, report, table)
            except KeyError:
                raise HTTPException(404, f"This report has no table '{table}'.")
            body = ("﻿" + text).encode("utf-8")  # BOM so Excel opens it as UTF-8
        elif fmt == "xlsx":
            body = await run_in_threadpool(exports.render_xlsx, report)
        else:
            body = await run_in_threadpool(
                partial(exports.render_pdf, report, tenant_name=tenant_name or "-", generated_by=me.name))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clean error, keep the traceback in the log
        import logging
        logging.getLogger(__name__).exception("Report export failed: %s", exc)
        raise HTTPException(500, f"Could not build the {fmt.upper()} file.") from exc
    name = _safe_filename(exports.filename_stem(report), ext)
    return Response(content=body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _check_runner(me: User) -> None:
    access.ensure_can_run_report(me, access.REPORT_STUDIO)


# --------------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------------
@router.get("/catalog")
async def catalog(me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    _check_runner(me)
    ctx = await report_request.load_context(db, me)
    current = report_intent._current(ctx.periods, ctx.today)
    return {
        "kinds": CATALOG,
        "formats": ["dashboard", "pdf", "xlsx", "csv"],
        "periods": [
            {"id": p.id, "label": p.label, "start_date": p.start_date, "end_date": p.end_date, "status": p.status,
             "is_current": bool(current and p.id == current.id)}
            for p in sorted(ctx.periods, key=lambda p: p.start_date, reverse=True)
        ],
        "entities": [{"id": i, "name": n} for i, n in ctx.entities.items()],
        "members": [{"id": i, "name": n} for i, n in ctx.users.items()],
        "examples": [
            "Generate the monthly task completion report.",
            "Prepare a report of pending tasks by team member.",
            "Compare this period with the previous period.",
            "Generate a close-status report for all entities.",
        ],
    }


@router.post("/interpret")
async def interpret(body: TextIn, me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    _check_runner(me)
    ctx = await report_request.load_context(db, me)
    return report_intent.interpret(body.text, ctx).to_dict()


@router.post("/ask")
async def ask(body: TextIn, me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    """Plain language in, dashboard payload out (a file format named in the text is returned as ``format``)."""
    result = await report_request.ask(db, me, body.text)
    out = result.interpretation.to_dict()
    out["report"] = result.report.to_dict() if result.report else None
    return out


@router.post("/generate")
async def generate(body: GenerateIn, me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)):
    _check_runner(me)
    tenant_id = me.tenant_id or ""
    tenant_name = await report_request.tenant_name(db, tenant_id)
    try:
        report = await build_report(db, me=me, spec=body.spec.to_spec(), tenant_name=tenant_name)
    except ReportError as exc:
        raise _fail(exc)
    if body.format == "json":
        return report.to_dict()
    response = await _file_response(report, body.format, me=me, tenant_name=tenant_name, table=body.table)
    await log_action(
        db, tenant_id=tenant_id, user_id=me.id, action="REPORT_EXPORTED", entity_type="Report", entity_id=None,
        meta={"kind": report.kind, "format": body.format, "period": (report.scope.get("period") or {}).get("label"),
              "entity": report.scope.get("entity"), "at": datetime.now(timezone.utc).isoformat()},
    )
    await db.commit()
    return response
