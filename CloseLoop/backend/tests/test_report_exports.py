"""Excel / PDF / CSV renderers for Report Studio reports."""
import csv
import io
import re
import unittest
from datetime import date, datetime, timezone

from openpyxl import load_workbook

from app.services import report_exports as ex
from app.services.report_studio import (
    Names, PeriodData, PlanRow, build_close_status, build_comparison, build_overdue, build_pending_by_member,
    build_period_completion,
)
from test_report_studio import NAMES, P10, P11, TODAY, StudioTestCase, T, dataset, period

NOW = datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc)


def all_reports(tasks=None, names=NAMES):
    tasks = tasks or dataset()
    cur = period(tasks)
    prev = period([T("p1", "COMPLETED", date(2026, 9, 10), completed_on=date(2026, 9, 9)),
                   T("p2", "NOT_STARTED", date(2026, 9, 25), "FR")], P10)
    kw = dict(generated_at=NOW)
    return {
        "completion": build_period_completion(cur, names, TODAY, **kw),
        "members": build_pending_by_member(cur, names, TODAY, **kw),
        "overdue": build_overdue(cur, names, TODAY, **kw),
        "close": build_close_status(cur, names, TODAY, **kw),
        "comparison": build_comparison(cur, prev, [prev, cur], names, TODAY, **kw),
    }


class ExcelTests(StudioTestCase):
    def load(self, report):
        return load_workbook(io.BytesIO(ex.render_xlsx(report)))

    def test_every_report_opens_with_a_summary_tables_and_native_charts(self):
        for name, report in all_reports().items():
            wb = self.load(report)
            self.assertEqual(wb.sheetnames[0], "Summary", name)
            self.assertIn("Charts", wb.sheetnames, name)
            self.assertEqual(len(wb["Charts"]._charts), len(report.charts), name)
            for table in report.tables:
                self.assertTrue(any(s.lower().startswith(table.title[:10].lower()) for s in wb.sheetnames), (name, table.title))

    def test_summary_sheet_has_kpis_findings_and_actions(self):
        report = all_reports()["completion"]
        ws = self.load(report)["Summary"]
        cells = {str(c.value): c for row in ws.iter_rows() for c in row if c.value is not None}
        self.assertEqual(ws["A1"].value, report.title)
        self.assertIn("Key findings", cells)
        self.assertIn("Recommended actions", cells)
        row = next(r for r in ws.iter_rows() if r[0].value == "Completion")
        self.assertEqual(row[1].value, 42.9)
        self.assertEqual(row[1].number_format, '0.0"%"')
        overdue = next(r for r in ws.iter_rows() if r[0].value == "Overdue")
        self.assertEqual(overdue[1].value, 3)

    def test_comparison_summary_shows_the_change_column(self):
        ws = self.load(all_reports()["comparison"])["Summary"]
        head = next(r for r in ws.iter_rows() if r[0].value == "Metric")
        self.assertEqual(head[2].value, "Change vs P10")
        row = next(r for r in ws.iter_rows() if r[0].value == "Completion")
        self.assertEqual(row[2].value, "-7.1 pts")  # 42.9% now against 50% in P10

    def test_table_sheet_is_filterable_frozen_and_typed(self):
        wb = self.load(all_reports()["overdue"])
        ws = wb["Overdue items"]
        self.assertEqual([c.value for c in ws[1]][:3], ["Task", "Entity", "Assigned to"])
        self.assertEqual(ws.max_row, 4)  # header + 3 overdue
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.auto_filter.ref, "A1:G4")
        due = ws.cell(row=2, column=6)
        self.assertEqual(due.value.date() if hasattr(due.value, "date") else due.value, date(2026, 10, 20))
        self.assertEqual(due.number_format, "yyyy-mm-dd")
        self.assertEqual(ws.cell(row=2, column=7).value, 4)

    def test_user_text_cannot_run_as_a_formula(self):
        evil = dataset() + [T("e", "NOT_STARTED", date(2026, 10, 1), who=["ux"])]
        names = Names(entities=NAMES.entities, users={**NAMES.users, "ux": "=HYPERLINK(\"http://x\",\"hi\")"})
        evil[-1] = T("e", "NOT_STARTED", date(2026, 10, 1), who=["ux"])
        evil[-1] = evil[-1].__class__(**{**evil[-1].__dict__, "title": "=cmd|' /C calc'!A0"})
        report = all_reports(evil, names)["overdue"]
        ws = self.load(report)["Overdue items"]
        texts = [c.value for row in ws.iter_rows(min_row=2) for c in row if isinstance(c.value, str)]
        self.assertTrue(any(t == "'=cmd|' /C calc'!A0" for t in texts), texts[:6])
        self.assertFalse(any(t.startswith("=") for t in texts))
        self.assertTrue(any(t.startswith("'=HYPERLINK") for t in texts))

    def test_chart_data_blocks_match_the_chart(self):
        report = all_reports()["completion"]
        ws = self.load(report)["Charts"]
        status = next(c for c in report.charts if c.id == "status_mix")
        values = [ws.cell(row=3 + i, column=2).value for i in range(len(status.labels))]
        self.assertEqual(values, [float(v) for v in status.series[0].values])

    def test_empty_report_still_renders(self):
        empty = build_period_completion(period([], plans=[]), NAMES, TODAY, generated_at=NOW)
        wb = self.load(empty)
        self.assertIn("Summary", wb.sheetnames)

    def test_sheet_names_are_unique_and_legal(self):
        used: set = set()
        a, b = ex._sheet_name("Pending work by team member / list: [x]?", used), ex._sheet_name("Pending work by team member / list: [x]?", used)
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), 31)
        self.assertFalse(re.search(r"[\[\]:*?/\\]", a + b))


class CsvTests(StudioTestCase):
    def rows(self, text):
        return list(csv.reader(io.StringIO(text)))

    def test_primary_table_with_readable_headers(self):
        rows = self.rows(ex.render_csv(all_reports()["overdue"]))
        self.assertEqual(rows[0][:3], ["Task", "Entity", "Assigned to"])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1][5], "2026-10-20")  # ISO dates
        self.assertEqual(rows[1][6], "4")

    def test_pick_a_table_by_id(self):
        rows = self.rows(ex.render_csv(all_reports()["completion"], "overdue_items"))
        self.assertEqual(rows[0][0], "Task")
        with self.assertRaises(KeyError):
            ex.render_csv(all_reports()["completion"], "nope")

    def test_missing_values_are_blank_and_formulas_are_defused(self):
        rows = self.rows(ex.render_csv(all_reports()["comparison"]))
        self.assertEqual(rows[0], ["Metric", "P10", "P11", "Change"])
        evil = ex.spreadsheet_safe
        self.assertEqual(evil("=1+1"), "'=1+1")
        self.assertEqual(evil("+cmd"), "'+cmd")
        self.assertEqual(evil("-cmd"), "'-cmd")
        self.assertEqual(evil("@sum"), "'@sum")
        self.assertEqual(evil("Normal"), "Normal")
        self.assertEqual(evil(-5), -5)  # numbers are never touched
        self.assertEqual(evil(""), "")


class PdfTests(StudioTestCase):
    def pages(self, data):
        return len(re.findall(rb"/Type\s*/Page\b", data))

    def test_every_report_renders_a_multi_page_pdf(self):
        for name, report in all_reports().items():
            pdf = ex.render_pdf(report, tenant_name="Acme Finance", generated_by="Test User")
            self.assertTrue(pdf.startswith(b"%PDF"), name)
            self.assertGreaterEqual(self.pages(pdf), 2, name)  # summary page(s) + charts page(s)

    def test_unusual_text_does_not_break_rendering(self):
        names = Names(entities={"DE": "DE <b>&</b>", "FR": "Français नेपाल"},
                      users={"u1": "A <i>lice</i>", "u2": "राम"})
        for name, report in all_reports(names=names).items():
            self.assertTrue(ex.render_pdf(report, tenant_name="Té <x>").startswith(b"%PDF"), name)

    def test_empty_and_long_reports_render(self):
        empty = build_period_completion(period([], plans=[]), NAMES, TODAY, generated_at=NOW)
        self.assertTrue(ex.render_pdf(empty).startswith(b"%PDF"))
        many = [T(f"t{i}", "NOT_STARTED", date(2026, 10, 1), who=["u1"]) for i in range(200)]
        report = build_overdue(period(many), NAMES, TODAY, generated_at=NOW)
        pdf = ex.render_pdf(report)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreaterEqual(self.pages(pdf), 3)

    def test_filename_is_ascii_and_descriptive(self):
        report = all_reports()["comparison"]
        self.assertEqual(ex.filename_stem(report), "period-comparison_P11_vs-P10_2026-10-26")
        self.assertEqual(ex.filename_stem(all_reports()["overdue"]), "overdue_P11_2026-10-26")


if __name__ == "__main__":
    unittest.main()
