"""Render a :class:`~report_studio.RichReport` as Excel, PDF or CSV.

All three read the same structure the dashboard does, so the files can never disagree with the screen.

  * ``render_xlsx``  - a Summary sheet (KPIs, findings, actions), one sheet per table with filters and
                       frozen headers, and a Charts sheet with NATIVE Excel charts the reader can restyle.
  * ``render_pdf``  - the audit-style CloseLoop page chrome plus KPI cards, charts, findings, actions
                       and tables, in a landscape A4 document.
  * ``render_csv``  - the report's primary table (or any table by id), UTF-8 with a BOM so Excel opens it.

Text typed by users (task titles, names) is untrusted: a cell starting with ``=``, ``+``, ``-`` or ``@``
would run as a formula when the file is opened in a spreadsheet, so such cells are neutralised in both
the CSV and the Excel output (``spreadsheet_safe``).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date, datetime
from io import BytesIO, StringIO
from typing import Any, Optional

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, LineChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer, Table, TableStyle

from .pdf_service import _AuditCanvas, _build_doc, _escape
from .report_studio import Chart, Column, Kpi, RichReport, Table as ReportTable

TONE_HEX = {
    "good": "1E8E5A", "warn": "D98A00", "bad": "C62F3E", "info": "2F6FDE", "neutral": "52606D", "muted": "9AA7B5",
}
TONE_TINT = {
    "good": "E7F6EE", "warn": "FFF4DC", "bad": "FDE8EA", "info": "E8F0FD", "neutral": "F1F4F7", "muted": "F1F4F7",
}
SERIES_CYCLE = ["2F6FDE", "9AA7B5", "1E8E5A", "D98A00", "C62F3E", "7B5CD6", "17A2B8"]
NAVY = "0F2A44"
PDF_ROW_LIMIT = 40


# --------------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------------
def spreadsheet_safe(value: Any) -> Any:
    """Neutralise formula injection: text that a spreadsheet would execute is prefixed with an apostrophe."""
    if isinstance(value, str) and value and (value[0] in "=+-@\t\r" or value.lstrip()[:1] in ("=", "+", "-", "@")):
        return "'" + value
    return value


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")


def filename_stem(report: RichReport) -> str:
    period = (report.scope.get("period") or {}).get("label") or ""
    compare = (report.scope.get("compare_period") or {}).get("label") or ""
    stem = "_".join(p for p in (report.kind.replace("_", "-"), period, f"vs-{compare}" if compare else "",
                                f"{report.generated_at:%Y-%m-%d}") if p)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", _ascii(stem)).strip("._-") or "report"


def series_color(chart: Chart, index: int, point: Optional[int] = None) -> str:
    s = chart.series[index]
    if point is not None and s.point_tones:
        return TONE_HEX.get(s.point_tones[point], SERIES_CYCLE[point % len(SERIES_CYCLE)])
    if s.tone:
        return TONE_HEX.get(s.tone, SERIES_CYCLE[index % len(SERIES_CYCLE)])
    return SERIES_CYCLE[index % len(SERIES_CYCLE)]


def _cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return value


# --------------------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------------------
def render_csv(report: RichReport, table_id: Optional[str] = None) -> str:
    table = next((t for t in report.tables if t.id == table_id), None) if table_id else report.primary_table()
    if table is None:
        raise KeyError(table_id or "primary")
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow([spreadsheet_safe(c.label) for c in table.columns])
    for row in table.rows:
        out = []
        for c in table.columns:
            v = _cell_value(row.get(c.key))
            if isinstance(v, (date, datetime)):
                v = v.isoformat()
            out.append("" if v is None else spreadsheet_safe(v))
        writer.writerow(out)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------------------------
_THIN = Side(style="thin", color="D5DBE3")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_NUMBER_FORMAT = {"percent": '0.0"%"', "int": "#,##0", "days": "0", "number": "#,##0.0", "date": "yyyy-mm-dd"}


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", start_color=hex_color, end_color=hex_color)


def _sheet_name(title: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:31] or "Sheet"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" {n}"
        name, n = base[: 31 - len(suffix)] + suffix, n + 1
    used.add(name.lower())
    return name


def _write_kpi_block(ws, report: RichReport, row: int) -> int:
    compare = (report.scope.get("compare_period") or {}).get("label")
    heads = ["Metric", "Value", f"Change vs {compare}" if compare else "Change", "Note"]
    for i, h in enumerate(heads, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), _fill(NAVY), _BORDER
    row += 1
    for k in report.kpis:
        cells = [spreadsheet_safe(k.label), k.value if k.value is not None else "n/a", k.delta_display or "", spreadsheet_safe(k.hint)]
        for i, v in enumerate(cells, start=1):
            c = ws.cell(row=row, column=i, value=v)
            c.border = _BORDER
        value_cell = ws.cell(row=row, column=2)
        value_cell.number_format = _NUMBER_FORMAT.get(k.unit, "#,##0") if k.value is not None else "General"
        value_cell.fill = _fill(TONE_TINT.get(k.tone, TONE_TINT["neutral"]))
        value_cell.font = Font(bold=True, color=TONE_HEX.get(k.tone, TONE_HEX["neutral"]))
        value_cell.alignment = Alignment(horizontal="right")
        if k.delta_display:
            ws.cell(row=row, column=3).font = Font(bold=True, color=TONE_HEX.get(k.delta_tone, TONE_HEX["neutral"]))
        row += 1
    return row


def _write_summary(wb: Workbook, report: RichReport) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    ws["A1"] = spreadsheet_safe(report.title)
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = spreadsheet_safe(report.subtitle)
    ws["A2"].font = Font(color="52606D")
    meta = f"Generated {report.generated_at:%Y-%m-%d %H:%M} UTC"
    if report.scope.get("generated_by"):
        meta += f" by {report.scope['generated_by']}"
    if report.scope.get("tenant"):
        meta += f" - {report.scope['tenant']}"
    ws["A3"] = spreadsheet_safe(meta)
    ws["A3"].font = Font(color="52606D", italic=True)

    row = _write_kpi_block(ws, report, 5) + 1

    def block(title: str, head: str, items: list[tuple[str, str, str]]) -> int:
        nonlocal row
        ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=12, color=NAVY)
        row += 1
        for tag, text, tone in items:
            a = ws.cell(row=row, column=1, value=tag)
            a.font, a.fill, a.border = Font(bold=True, color=TONE_HEX[tone]), _fill(TONE_TINT[tone]), _BORDER
            b = ws.cell(row=row, column=2, value=spreadsheet_safe(text))
            b.alignment, b.border = Alignment(wrap_text=True, vertical="top"), _BORDER
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
            ws.row_dimensions[row].height = max(18, 15 * (1 + len(text) // 95))
            row += 1
        row += 1
        return row

    sev_tone = {"good": "good", "info": "info", "warn": "warn", "bad": "bad"}
    block("Key findings", "Finding", [(f.severity.upper(), f.text, sev_tone.get(f.severity, "info")) for f in report.findings])
    pri_tone = {"high": "bad", "medium": "warn", "low": "info"}
    block("Recommended actions", "Action", [(r.priority.upper(), r.text, pri_tone.get(r.priority, "info")) for r in report.recommendations])
    if report.notes:
        ws.cell(row=row, column=1, value="How to read this report").font = Font(bold=True, size=12, color=NAVY)
        row += 1
        for n in report.notes:
            c = ws.cell(row=row, column=1, value=spreadsheet_safe(n))
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
            ws.row_dimensions[row].height = max(18, 15 * (1 + len(n) // 110))
            row += 1
    for letter, width in zip("ABCD", (30, 16, 22, 60)):
        ws.column_dimensions[letter].width = width


def _write_table_sheet(wb: Workbook, table: ReportTable, used: set[str]) -> None:
    ws = wb.create_sheet(_sheet_name(table.title, used))
    for i, col in enumerate(table.columns, start=1):
        c = ws.cell(row=1, column=i, value=spreadsheet_safe(col.label))
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), _fill(NAVY), _BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 32
    for r, row in enumerate(table.rows, start=2):
        for i, col in enumerate(table.columns, start=1):
            v = _cell_value(row.get(col.key))
            if isinstance(v, str):
                v = spreadsheet_safe(v)
            cell = ws.cell(row=r, column=i, value=v)
            cell.border = _BORDER
            fmt = _NUMBER_FORMAT.get(col.type)
            if fmt and v is not None:
                cell.number_format = fmt
            if col.type in ("percent", "int", "days", "number"):
                cell.alignment = Alignment(horizontal="right")
    for i, col in enumerate(table.columns, start=1):
        longest = max([len(str(col.label))] + [len(str(_cell_value(r.get(col.key)) or "")) for r in table.rows[:300]])
        ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, longest + 3))
    if table.rows:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(table.columns))}{len(table.rows) + 1}"
    if table.total_rows:
        ws.cell(row=len(table.rows) + 3, column=1, value=f"Showing {len(table.rows)} of {table.total_rows} rows.")


def _write_charts_sheet(wb: Workbook, charts: list[Chart], used: set[str]) -> None:
    if not charts:
        return
    ws = wb.create_sheet(_sheet_name("Charts", used))
    ws.sheet_view.showGridLines = False
    row = 1
    for chart in charts:
        ws.cell(row=row, column=1, value=chart.title).font = Font(bold=True, size=12, color=NAVY)
        header = row + 1
        ws.cell(row=header, column=1, value="").font = Font(bold=True)
        for j, s in enumerate(chart.series, start=2):
            ws.cell(row=header, column=j, value=spreadsheet_safe(s.name)).font = Font(bold=True)
        for i, label in enumerate(chart.labels, start=1):
            ws.cell(row=header + i, column=1, value=spreadsheet_safe(label))
            for j, s in enumerate(chart.series, start=2):
                ws.cell(row=header + i, column=j, value=s.values[i - 1])
        n_rows, n_series = len(chart.labels), len(chart.series)
        xl = _native_chart(chart)
        data = Reference(ws, min_col=2, max_col=1 + n_series, min_row=header, max_row=header + n_rows)
        cats = Reference(ws, min_col=1, min_row=header + 1, max_row=header + n_rows)
        xl.add_data(data, titles_from_data=True)
        xl.set_categories(cats)
        _color_native(xl, chart)
        xl.title = chart.title
        xl.width, xl.height = 18, 8.5
        ws.add_chart(xl, f"{get_column_letter(n_series + 3)}{row}")
        row = header + max(n_rows + 3, 20)
    ws.column_dimensions["A"].width = 28


def _native_chart(chart: Chart):
    if chart.kind == "donut":
        return DoughnutChart()
    if chart.kind == "line":
        c = LineChart()
        c.y_axis.number_format = '0.0"%"' if chart.unit == "percent" else "0"
        return c
    c = BarChart()
    c.type = "bar" if chart.kind.endswith("hbar") else "col"
    if chart.kind.startswith("stacked"):
        c.grouping, c.overlap = "stacked", 100
    if chart.unit == "percent":
        c.y_axis.number_format = '0"%"'
        c.y_axis.scaling.min, c.y_axis.scaling.max = 0, 100
    return c


def _color_native(xl, chart: Chart) -> None:
    for idx, s in enumerate(xl.series):
        if chart.kind == "donut":
            for p in range(len(chart.labels)):
                point = DataPoint(idx=p)
                point.graphicalProperties.solidFill = series_color(chart, 0, p)
                s.dPt.append(point)
            continue
        color = series_color(chart, idx)
        if chart.kind == "line":
            s.graphicalProperties.line.solidFill = color
            s.graphicalProperties.line.width = 28000
            s.smooth = False
        else:
            s.graphicalProperties.solidFill = color
    if chart.kind == "line":
        xl.dispBlanksAs = "gap"


def render_xlsx(report: RichReport) -> bytes:
    wb = Workbook()
    used = {"summary"}
    _write_summary(wb, report)
    for table in report.tables:
        _write_table_sheet(wb, table, used)
    _write_charts_sheet(wb, report.charts, used)
    wb.properties.title = _ascii(report.title)[:200]
    wb.properties.creator = "CloseLoop"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------------------
class _ReportCanvas(_AuditCanvas):
    HEADER_TITLE = "CLOSELOOP  -  REPORT"


def _t(text: Any) -> str:
    """Paragraph-safe text: XML-escaped and reduced to what the built-in PDF fonts can draw."""
    s = "" if text is None else str(text)
    return _escape(s.encode("latin-1", "replace").decode("latin-1"))


def _plain(text: Any) -> str:
    s = "" if text is None else str(text)
    return s.encode("latin-1", "replace").decode("latin-1")


def _hex(h: str) -> colors.Color:
    return colors.HexColor("#" + h)


def _short(label: str, limit: int = 20) -> str:
    label = _plain(label)
    return label if len(label) <= limit else label[: limit - 1] + "."


def _kpi_cell(k: Kpi, styles: dict[str, ParagraphStyle]) -> Table:
    tone = TONE_HEX.get(k.tone, TONE_HEX["neutral"])
    delta = ""
    if k.delta_display:
        delta = f'<font size="8" color="#{TONE_HEX.get(k.delta_tone, TONE_HEX["neutral"])}"><b>{_t(k.delta_display)}</b></font>'
    hint = f'<br/><font size="7" color="#667788">{_t(k.hint)}</font>' if k.hint else ""
    para = Paragraph(
        f'<font size="8" color="#52606D">{_t(k.label).upper()}</font><br/>'
        f'<font size="19" color="#{tone}"><b>{_t(k.display)}</b></font> {delta}{hint}', styles["kpi"])
    cell = Table([[para]], colWidths=[62 * mm])
    cell.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _hex(TONE_TINT.get(k.tone, TONE_TINT["neutral"]))),
        ("LINEBEFORE", (0, 0), (0, -1), 3, _hex(tone)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return cell


def _legend(d: Drawing, pairs: list[tuple[colors.Color, str]], x: float, y: float) -> None:
    lg = Legend()
    lg.x, lg.y = x, y
    lg.fontName, lg.fontSize = "Helvetica", 7
    lg.alignment = "right"
    lg.deltax, lg.deltay, lg.dxTextSpace = 70, 9, 4
    lg.boxAnchor = "nw"
    lg.columnMaximum = 8
    lg.colorNamePairs = [(c, _short(n, 26)) for c, n in pairs]
    d.add(lg)


def _drawing(chart: Chart, width: float, height: float) -> Drawing:
    d = Drawing(width, height)
    d.add(String(0, height - 10, _plain(chart.title), fontName="Helvetica-Bold", fontSize=9, fillColor=_hex(NAVY)))
    top = height - 22
    labels = [_plain(l) for l in chart.labels]
    palette = [_hex(series_color(chart, i)) for i in range(len(chart.series))]

    if chart.kind == "donut":
        values = [v or 0 for v in chart.series[0].values]
        total = sum(values)
        if total <= 0:
            d.add(String(10, top / 2, "No data", fontName="Helvetica", fontSize=9, fillColor=_hex("9AA7B5")))
            return d
        pie = Pie()
        size = min(top - 6, width * 0.42)
        pie.x, pie.y, pie.width, pie.height = 8, (top - size) / 2, size, size
        pie.data = values
        pie.sideLabels = False
        pie.slices.strokeColor, pie.slices.strokeWidth = colors.white, 1
        slice_colors = [_hex(series_color(chart, 0, i)) for i in range(len(values))]
        for i, c in enumerate(slice_colors):
            pie.slices[i].fillColor = c
        d.add(pie)
        _legend(d, [(slice_colors[i], f"{labels[i]}: {int(values[i]):,} ({values[i] / total * 100:.0f}%)")
                    for i in range(len(values))], size + 24, top - 8)
        return d

    series = chart.series
    horizontal = chart.kind.endswith("hbar")
    stacked = chart.kind.startswith("stacked")
    many = len(labels) > 5
    if chart.kind == "line":
        lc = HorizontalLineChart()
        lc.x, lc.y, lc.width, lc.height = 36, 34, width - 52, top - 52
        lc.data = [tuple(v for v in s.values) for s in series]
        lc.categoryAxis.categoryNames = [_short(l, 10) for l in labels]
        lc.categoryAxis.labels.fontSize = 7
        lc.categoryAxis.labels.angle = 30 if many else 0
        lc.categoryAxis.labels.boxAnchor = "ne" if many else "n"
        lc.valueAxis.labels.fontSize = 7
        lc.valueAxis.valueMin = 0
        if chart.unit == "percent":
            lc.valueAxis.valueMax = 100
        for i, c in enumerate(palette):
            lc.lines[i].strokeColor, lc.lines[i].strokeWidth = c, 2
        d.add(lc)
        if len(series) > 1:
            _legend(d, [(palette[i], s.name) for i, s in enumerate(series)], 40, height - 14)
        return d

    if horizontal:
        bc = HorizontalBarChart()
        labels = list(reversed(labels))  # first row at the top
        data = [tuple(reversed([v or 0 for v in s.values])) for s in series]
    else:
        bc = VerticalBarChart()
        data = [tuple(v or 0 for v in s.values) for s in series]
    left = 92 if horizontal else 36
    bc.x, bc.y = left, 34 if not horizontal else 22
    bc.width, bc.height = width - left - 16, top - (52 if not horizontal else 38)
    bc.data = data
    bc.categoryAxis.categoryNames = [_short(l, 16 if horizontal else 12) for l in labels]
    bc.categoryAxis.labels.fontSize = 7
    if not horizontal:
        bc.categoryAxis.labels.angle = 30 if many else 0
        bc.categoryAxis.labels.boxAnchor = "ne" if many else "n"
    bc.valueAxis.labels.fontSize = 7
    bc.valueAxis.valueMin = 0
    if chart.unit == "percent":
        bc.valueAxis.valueMax = 100
    if stacked:
        bc.categoryAxis.style = "stacked"
    bc.groupSpacing, bc.barSpacing = 6, 1
    for i, c in enumerate(palette):
        bc.bars[i].fillColor, bc.bars[i].strokeColor = c, colors.white
    if len(series) == 1:
        bc.barLabelFormat = "%.1f" if chart.unit == "percent" else "%d"
        bc.barLabels.fontSize = 7
        bc.barLabels.nudge = 6 if not horizontal else 12
    d.add(bc)
    if len(series) > 1:
        _legend(d, [(palette[i], s.name) for i, s in enumerate(series)], left, height - 14)
    return d


def _table_flowable(table: ReportTable, styles: dict[str, ParagraphStyle], width: float) -> Any:
    cols = table.columns
    weights = [2.6 if c.type == "text" and c.key in ("title", "blocker", "assignees") else (1.8 if c.type in ("text", "badge") else 1.0) for c in cols]
    scale = width / sum(weights)
    col_w = [w * scale for w in weights]

    def fmt(col: Column, v: Any) -> str:
        v = _cell_value(v)
        if v is None or v == "":
            return "-" if col.type != "text" else ""
        if col.type == "percent" and isinstance(v, (int, float)):
            return f"{v:.1f}%"
        if col.type in ("int", "days") and isinstance(v, (int, float)):
            return f"{int(v):,}"
        if col.type == "number" and isinstance(v, (int, float)):
            return f"{v:+,.1f}" if col.key in ("change", "delta", "delta_overdue") else f"{v:,.1f}".rstrip("0").rstrip(".")
        if isinstance(v, (date, datetime)):
            return v.strftime("%d %b %Y")
        return str(v)

    data = [[Paragraph(f"<b>{_t(c.label)}</b>", styles["th"]) for c in cols]]
    for row in table.rows[:PDF_ROW_LIMIT]:
        data.append([Paragraph(_t(fmt(c, row.get(c.key))), styles["td_right" if c.type in ("percent", "int", "days", "number") else "td"])
                     for c in cols])
    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _hex(NAVY)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, _hex("C9D2DC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _hex("F4F6F9")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("RB", parent=base["BodyText"], fontSize=9, leading=12)
    return {
        "title": ParagraphStyle("RT", parent=base["Heading1"], fontSize=17, leading=21, textColor=_hex(NAVY), spaceAfter=2),
        "sub": ParagraphStyle("RS", parent=body, textColor=_hex("52606D")),
        "h2": ParagraphStyle("RH", parent=base["Heading2"], fontSize=12, textColor=_hex(NAVY), spaceBefore=8, spaceAfter=4),
        "body": body,
        "kpi": ParagraphStyle("RK", parent=body, leading=22),
        "th": ParagraphStyle("TH", parent=body, fontSize=7.5, leading=9, textColor=colors.white),
        "td": ParagraphStyle("TD", parent=body, fontSize=7.5, leading=9),
        "td_right": ParagraphStyle("TDR", parent=body, fontSize=7.5, leading=9, alignment=2),
        "note": ParagraphStyle("RN", parent=body, fontSize=7.5, leading=10, textColor=_hex("52606D")),
    }


def _marked_rows(items: list[tuple[str, str, str]], styles: dict[str, ParagraphStyle], width: float) -> Table:
    data = [["", Paragraph(f'<font size="7" color="#{TONE_HEX[tone]}"><b>{_t(tag)}</b></font>', styles["body"]),
             Paragraph(_t(text), styles["body"])] for tag, text, tone in items]
    t = Table(data, colWidths=[2.2 * mm, 20 * mm, width - 22.2 * mm])
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, _hex("E1E6EC")),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i, (_tag, _text, tone) in enumerate(items):
        style.append(("BACKGROUND", (0, i), (0, i), _hex(TONE_HEX[tone])))
    t.setStyle(TableStyle(style))
    return t


def render_pdf(report: RichReport, *, tenant_name: str = "-", generated_by: str = "-") -> bytes:
    buffer = BytesIO()
    period = report.scope.get("period") or {}
    meta = {
        "report_type": report.kind, "report_type_label": _plain(report.title), "tenant_name": _plain(tenant_name),
        "generated_by_name": _plain(generated_by), "generated_at_str": report.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "period_label": _plain(period.get("label")) if period.get("label") else None,
        "entity_name": _plain(report.scope.get("entity")) if report.scope.get("entity") not in (None, "All entities") else None,
        "target_user_name": _plain(report.scope.get("member")) if report.scope.get("member") else None,
    }
    doc = _build_doc(buffer, meta)
    doc._canvas_maker = lambda *a, **k: _ReportCanvas(*a, header_meta=meta, **k)  # type: ignore[attr-defined]
    styles = _pdf_styles()
    width = doc.width
    story: list[Any] = [Paragraph(_t(report.title), styles["title"]), Paragraph(_t(report.subtitle), styles["sub"]), Spacer(1, 4 * mm)]

    if report.kpis:
        cells = [_kpi_cell(k, styles) for k in report.kpis]
        per_row = 4
        rows = [cells[i:i + per_row] + [""] * (per_row - len(cells[i:i + per_row])) for i in range(0, len(cells), per_row)]
        grid = Table(rows, colWidths=[width / per_row] * per_row)
        grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0)]))
        story += [grid, Spacer(1, 3 * mm)]

    sev_tone = {"good": "good", "info": "info", "warn": "warn", "bad": "bad"}
    if report.findings:
        story.append(Paragraph("Key findings", styles["h2"]))
        story.append(_marked_rows([(f.severity.upper(), f.text, sev_tone.get(f.severity, "info")) for f in report.findings], styles, width))
    pri_tone = {"high": "bad", "medium": "warn", "low": "info"}
    if report.recommendations:
        story.append(Paragraph("Recommended actions", styles["h2"]))
        story.append(_marked_rows([(r.priority.upper(), r.text, pri_tone.get(r.priority, "info")) for r in report.recommendations], styles, width))

    if report.charts:
        story.append(PageBreak())
        story.append(Paragraph("Charts", styles["h2"]))
        chart_w, chart_h = (width - 6 * mm) / 2, 62 * mm
        drawings = [_drawing(c, chart_w, chart_h) for c in report.charts]
        for i in range(0, len(drawings), 2):
            pair = drawings[i:i + 2]
            row = Table([pair + [""] * (2 - len(pair))], colWidths=[chart_w + 6 * mm / 2] * 2)
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
            story.append(KeepTogether(row))

    for table in report.tables:
        story.append(Paragraph(_t(table.title), styles["h2"]))
        if not table.rows:
            story.append(Paragraph("<i>Nothing to list.</i>", styles["body"]))
            continue
        story.append(_table_flowable(table, styles, width))
        shown, total = min(len(table.rows), PDF_ROW_LIMIT), table.total_rows or len(table.rows)
        extra = f"Showing {shown} of {total} rows - the Excel and CSV exports contain every row." if total > shown else ""
        line = " ".join(x for x in (table.note, extra) if x)
        if line:
            story.append(Paragraph(_t(line), styles["note"]))

    if report.notes:
        story.append(Paragraph("How to read this report", styles["h2"]))
        for n in report.notes:
            story.append(Paragraph("- " + _t(n), styles["note"]))
    doc.build(story, canvasmaker=doc._canvas_maker)  # type: ignore[attr-defined]
    return buffer.getvalue()
