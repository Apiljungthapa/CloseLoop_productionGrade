"""Answer "generate the monthly report" typed into Close Bhai, without calling the language-model gateway.

A report request is not a question about data or documents: it asks the app to PRODUCE something. So the chat
endpoint hands it here. We build the report with the same engine and permissions as the Report Studio page, and
reply in the chat's own event stream:

    status  ->  report (a card: title, headline figures, findings, download buttons)  ->  token (a short summary)  ->  done

The frames are fully built before anything is sent (the database session is only valid during the request).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from . import report_request
from .report_intent import DOWNLOAD_FORMATS
from .report_studio import RichReport

NOT_ALLOWED = (
    "Reports are available to Team Leads and Super Admins. Ask your Team Lead for the report you need, "
    "or ask me a question about your organization's tasks and I'll look it up."
)


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _summary_text(report: RichReport, assumptions: list[str]) -> str:
    lines = [f"Here is the **{report.title}**.", ""]
    for k in report.kpis[:4]:
        change = f" ({k.delta_display})" if k.delta_display else ""
        lines.append(f"- **{k.label}:** {k.display}{change}")
    findings = [f for f in report.findings if f.severity in ("bad", "warn")] or report.findings
    if findings:
        lines += ["", "**What stands out**"] + [f"- {f.text}" for f in findings[:3]]
    if report.recommendations:
        lines += ["", f"**Suggested next step:** {report.recommendations[0].text}"]
    if assumptions:
        lines += ["", "_" + " ".join(assumptions) + "_"]
    lines += ["", "Open the full dashboard, or download it, with the buttons below."]
    return "\n".join(lines)


def _card(report: RichReport, interp: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec": interp["spec"],
        "format": interp["format"],
        "formats": interp["formats"],
        "downloads": list(DOWNLOAD_FORMATS),
        "title": report.title,
        "subtitle": report.subtitle,
        "message": interp["message"],
        "kpis": [
            {"key": k.key, "label": k.label, "display": k.display, "tone": k.tone,
             "delta_display": k.delta_display, "delta_tone": k.delta_tone}
            for k in report.kpis[:4]
        ],
        "findings": [{"severity": f.severity, "text": f.text} for f in report.findings[:3]],
    }


def frames_for(interp: dict[str, Any], report: Optional[RichReport], elapsed_ms: int) -> list[str]:
    """The SSE frames for an interpreted request (pure; unit-tested without a database)."""
    frames = [sse("status", {"step": "building_report", "message": "Preparing your report..."})]
    if report is None:
        frames.append(sse("token", {"text": interp.get("clarification") or "I couldn't work out which report you want."}))
        frames.append(sse("done", {"route": "report", "needs_clarification": True, "warnings": [], "elapsed_ms": elapsed_ms}))
        return frames
    frames.append(sse("report", _card(report, interp)))
    frames.append(sse("token", {"text": _summary_text(report, interp.get("assumptions") or [])}))
    frames.append(sse("done", {"route": "report", "needs_clarification": False, "warnings": [], "elapsed_ms": elapsed_ms}))
    return frames


async def reply_frames(db: AsyncSession, me: User, question: str) -> list[str]:
    import time

    started = time.monotonic()
    try:
        result = await report_request.ask(db, me, question)
    except HTTPException:  # a role that may not run reports
        return [
            sse("status", {"step": "building_report", "message": "Preparing your report..."}),
            sse("token", {"text": NOT_ALLOWED}),
            sse("done", {"route": "report", "needs_clarification": False, "warnings": [], "elapsed_ms": 0}),
        ]
    return frames_for(result.interpretation.to_dict(), result.report, int((time.monotonic() - started) * 1000))
