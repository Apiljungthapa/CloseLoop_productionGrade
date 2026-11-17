"""
PDF rendering for the Report Engine.

Builds an audit-grade ReportLab Platypus document with:

  * a fixed top **system header** band (CLOSELOOP wordmark, tenant name,
    report type, target period/entity, generated-at UTC, generated-by
    user) drawn on every page;
  * a diagonal grey **watermark** of the executing user's name across
    every page;
  * a structured body containing a title, an optional summary block, and
    the row table.

All rendering is **synchronous and in-memory** — the function returns
``bytes`` so the router can stream them directly in the HTTP response
without touching disk or any background worker (per Task 3 §3.1).
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Iterable, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# ============================================================
# Header / footer / watermark canvas callback
# ============================================================
class _AuditCanvas(Canvas):
    """
    Canvas subclass that stamps the immutable audit header, the page
    footer and the diagonal user-name watermark on every page.

    Built using ReportLab's two-pass "page count" pattern so we can
    render `Page X of Y` in the footer.
    """

    HEADER_TITLE = "CLOSELOOP  -  PERIOD CLOSE REPORT"

    def __init__(self, *args, header_meta: dict[str, Any], **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages: list[dict] = []
        self._header_meta = header_meta

    def showPage(self) -> None:  # type: ignore[override]
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:  # type: ignore[override]
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_page_chrome(total)
            super().showPage()
        super().save()

    # --------------------------------------------------------------
    def _draw_page_chrome(self, total_pages: int) -> None:
        width, height = self._pagesize
        meta = self._header_meta

        # ---- Top header band ----------------------------------------
        self.saveState()
        self.setFillColor(colors.HexColor("#0F2A44"))
        self.rect(0, height - 22 * mm, width, 22 * mm, stroke=0, fill=1)
        self.setFillColor(colors.white)
        self.setFont("Helvetica-Bold", 12)
        self.drawString(15 * mm, height - 10 * mm, self.HEADER_TITLE)
        self.setFont("Helvetica", 8)
        self.drawString(
            15 * mm,
            height - 16 * mm,
            f"Report: {meta.get('report_type_label', '')}",
        )
        # Right-aligned tenant + generation block
        self.setFont("Helvetica-Bold", 10)
        self.drawRightString(
            width - 15 * mm,
            height - 10 * mm,
            f"Tenant: {meta.get('tenant_name', '-')}",
        )
        self.setFont("Helvetica", 8)
        self.drawRightString(
            width - 15 * mm,
            height - 15 * mm,
            f"Generated: {meta.get('generated_at_str', '-')} UTC",
        )
        self.drawRightString(
            width - 15 * mm,
            height - 19 * mm,
            f"By: {meta.get('generated_by_name', '-')}",
        )
        self.restoreState()

        # ---- Sub-header context line --------------------------------
        ctx_parts = []
        if meta.get("period_label"):
            ctx_parts.append(f"Period: {meta['period_label']}")
        if meta.get("entity_name"):
            ctx_parts.append(f"Entity: {meta['entity_name']}")
        if meta.get("target_user_name"):
            ctx_parts.append(f"Subject: {meta['target_user_name']}")
        if ctx_parts:
            self.saveState()
            self.setFillColor(colors.HexColor("#E5ECF4"))
            self.rect(0, height - 30 * mm, width, 8 * mm, stroke=0, fill=1)
            self.setFillColor(colors.HexColor("#0F2A44"))
            self.setFont("Helvetica-Bold", 9)
            self.drawString(15 * mm, height - 26 * mm, "  |  ".join(ctx_parts))
            self.restoreState()

        # ---- Diagonal watermark -------------------------------------
        watermark = meta.get("generated_by_name") or "CLOSELOOP"
        self.saveState()
        self.setFont("Helvetica-Bold", 60)
        # Drawn after the page content, so it is see-through: charts and table text stay readable.
        self.setFillColor(colors.HexColor("#8FA0B3"))
        self.setFillAlpha(0.16)
        self.translate(width / 2, height / 2)
        self.rotate(35)
        self.drawCentredString(0, 0, watermark.upper())
        self.restoreState()

        # ---- Footer -------------------------------------------------
        self.saveState()
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#555555"))
        self.drawString(
            15 * mm,
            10 * mm,
            "This report is auto-generated by CloseLoop. Header and watermark are immutable audit evidence.",
        )
        self.drawRightString(
            width - 15 * mm, 10 * mm, f"Page {self._pageNumber} of {total_pages}"
        )
        self.restoreState()


# ============================================================
# Doc template
# ============================================================
def _build_doc(buffer: BytesIO, header_meta: dict[str, Any]) -> BaseDocTemplate:
    """Construct a landscape A4 doc with the audit chrome bound to every page."""
    page_size = landscape(A4)
    doc = BaseDocTemplate(
        buffer,
        pagesize=page_size,
        topMargin=35 * mm,
        bottomMargin=18 * mm,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        title=f"{header_meta.get('report_type_label', 'Report')} - {header_meta.get('tenant_name', '')}",
        author=header_meta.get("generated_by_name", "CloseLoop"),
    )

    def _canvas_maker(*args, **kwargs):
        return _AuditCanvas(*args, header_meta=header_meta, **kwargs)

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="body",
        showBoundary=0,
    )
    doc.addPageTemplates([PageTemplate(id="audit", frames=[frame])])
    # Stash canvas factory for build()
    doc._canvas_maker = _canvas_maker  # type: ignore[attr-defined]
    return doc


# ============================================================
# Public entrypoint
# ============================================================
def render_report_pdf(
    *,
    report_type: str,
    report_type_label: str,
    tenant_name: str,
    generated_by_name: str,
    generated_at: datetime,
    columns: list[str],
    rows: list[dict[str, Any]],
    period_label: Optional[str] = None,
    entity_name: Optional[str] = None,
    target_user_name: Optional[str] = None,
    summary: Optional[dict[str, Any]] = None,
    parameters: Optional[dict[str, Any]] = None,
) -> bytes:
    """
    Render a report to PDF bytes — fully synchronous and in-memory.

    Parameters are kwarg-only to keep call sites readable; the router
    is the only caller. Returns the encoded PDF as ``bytes``.
    """
    buffer = BytesIO()
    header_meta = {
        "report_type": report_type,
        "report_type_label": report_type_label,
        "tenant_name": tenant_name,
        "generated_by_name": generated_by_name,
        "generated_at_str": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "period_label": period_label,
        "entity_name": entity_name,
        "target_user_name": target_user_name,
    }
    doc = _build_doc(buffer, header_meta)
    styles = getSampleStyleSheet()
    # Create a fresh title style instead of mutating the shared
    # singleton — calling getSampleStyleSheet() returns the SAME
    # instance per process, so mutating "Heading1" here would leak
    # across requests and could even raise on re-registration.
    title_style = ParagraphStyle(
        name="ReportTitle",
        parent=styles["Heading1"],
        fontSize=14,
        textColor=colors.HexColor("#0F2A44"),
    )
    body_style = ParagraphStyle(
        name="Body", parent=styles["BodyText"], fontSize=9, leading=11
    )
    summary_style = ParagraphStyle(
        name="Summary",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#333333"),
    )

    story: list[Any] = []
    story.append(Paragraph(report_type_label, title_style))
    story.append(Spacer(1, 4 * mm))

    if parameters:
        param_bits = [f"<b>{k}</b>: {_safe_str(v)}" for k, v in parameters.items()]
        story.append(Paragraph("  &nbsp;|&nbsp;  ".join(param_bits), body_style))
        story.append(Spacer(1, 3 * mm))

    if summary:
        story.append(Paragraph("<b>Summary</b>", body_style))
        for k, v in summary.items():
            story.append(
                Paragraph(f"&nbsp;&nbsp;{k}: <b>{_safe_str(v)}</b>", summary_style)
            )
        story.append(Spacer(1, 4 * mm))

    if rows:
        # Build the table data
        header_row = [Paragraph(f"<b>{c}</b>", body_style) for c in columns]
        data: list[list[Any]] = [header_row]
        for r in rows:
            row_cells = []
            for c in columns:
                val = r.get(c, "")
                row_cells.append(Paragraph(_safe_str(val), body_style))
            data.append(row_cells)

        # Try to give each column an equal share of the available width.
        avail_width = doc.width
        col_width = avail_width / max(len(columns), 1)
        col_widths = [col_width] * len(columns)
        tbl = Table(data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F2A44")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#888888")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F4F6F9")],
                    ),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(tbl)
    else:
        story.append(
            Paragraph("<i>No rows matched the report filters.</i>", body_style)
        )

    # Build with the custom canvas
    canvas_maker = getattr(doc, "_canvas_maker", None)
    if canvas_maker is None:  # pragma: no cover — defensive
        doc.build(story)
    else:
        doc.build(story, canvasmaker=canvas_maker)
    return buffer.getvalue()


# ============================================================
# Helpers
# ============================================================
def _safe_str(value: Any) -> str:
    """Coerce arbitrary cell values to a PDF-safe string.

    The result is XML-escaped so ReportLab's ``Paragraph`` parser does
    not choke on user content containing ``<``, ``>``, ``&`` or quotes.
    Lists/dicts are recursively flattened, with their separators kept
    plain (no escape needed for "," / "; " / "=").
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_safe_str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{_escape(str(k))}={_safe_str(v)}" for k, v in value.items())
    return _escape(str(value))


def _escape(text: str) -> str:
    """Escape XML-sensitive characters that would otherwise break Paragraph rendering."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&#39;")
        .replace('"', "&quot;")
    )


# ============================================================
# Report type labels — keep human-readable strings in one place
# ============================================================
REPORT_TYPE_LABELS: dict[str, str] = {
    "period_close_completion": "Period Close Completion Report",
    "current_period_status": "Current Period Status Report",
    "overdue_task": "Overdue Task Report",
    "user_workload": "User Workload and Progress Report",
    "year_end_rollup": "Year-End Rollup Report",
    "time_entry": "Time Entry Report",
}


def label_for(report_type: str) -> str:
    return REPORT_TYPE_LABELS.get(report_type, report_type.replace("_", " ").title())


# ============================================================
# CSV helper (kept here so all "export format" logic lives together)
# ============================================================
def render_report_csv(columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> str:
    """Render rows as CSV text. Stable column ordering = `columns`."""
    import csv
    from io import StringIO

    buf = StringIO()
    cols = list(columns)
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: _csv_cell(r.get(c, "")) for c in cols})
    return buf.getvalue()


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in value.items())
    return value
