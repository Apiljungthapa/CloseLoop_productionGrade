"""Plain-language report requests -> report specs."""
import unittest
from datetime import date

from app.services.report_intent import Context, detect_formats, detect_kind, interpret, looks_like_report_request
from app.services.report_studio import (
    KIND_CLOSE_STATUS, KIND_COMPARISON, KIND_COMPLETION, KIND_MEMBERS, KIND_OVERDUE, PeriodInfo,
)

TODAY = date(2026, 10, 26)
PERIODS = [
    PeriodInfo("p9", "P9", date(2026, 8, 10), date(2026, 9, 6), "CLOSED"),
    PeriodInfo("p10", "P10", date(2026, 9, 7), date(2026, 10, 4), "CLOSED"),
    PeriodInfo("p11", "P11", date(2026, 10, 5), date(2026, 11, 1), "OPEN"),
    PeriodInfo("p12", "P12", date(2026, 11, 2), date(2026, 11, 29), "CLOSED"),
]
CTX = Context(
    periods=PERIODS, entities={"e-de": "DE", "e-fr": "FR", "e-nl": "NL", "e-hq": "Head Office"},
    users={"u1": "Binita Gopali", "u2": "Devraj Khatri", "u3": "Binita Rai", "u4": "Sisam Khatri"}, today=TODAY,
)


class IsItAReportRequest(unittest.TestCase):
    def test_requests(self):
        for text in [
            "Generate the monthly task completion report.",
            "Prepare a report of pending tasks by team member.",
            "Compare this period with the previous period.",
            "Generate a close-status report for all entities.",
            "give me an overdue report in pdf",
            "I need a report on this period",
            "can you generate the completion report as excel",
            "export the overdue tasks as csv",
            "Please create a P10 vs P11 comparison report",
            "monthly report",
            "weekly status report for FR",
            "download pending tasks by member as excel",
            "show me a summary of this period",
            "build me a workload report",
        ]:
            self.assertTrue(looks_like_report_request(text), text)

    def test_questions_and_ordinary_chat_are_not_requests(self):
        for text in [
            "what is a report", "how do I generate a report?", "where can I find the reports",
            "explain the overdue report", "who is devraj khatri", "how many tasks are overdue", "hello",
            "report a bug", "I want to report an issue", "can I see the reports page", "tell me about close packs",
            "how is this period going", "what is the status of all entities", "list all team members",
            "give me the number of overdue tasks", "show me the overdue tasks", "", "hi there",
        ]:
            self.assertFalse(looks_like_report_request(text), text)


class Detection(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "monthly task completion report": KIND_COMPLETION,
            "pending tasks by team member": KIND_MEMBERS,
            "workload report": KIND_MEMBERS,
            "compare this period with the previous period": KIND_COMPARISON,
            "october vs september": KIND_COMPARISON,
            "close-status report for all entities": KIND_CLOSE_STATUS,
            "close status": KIND_CLOSE_STATUS,
            "how ready are the entities to close": KIND_CLOSE_STATUS,
            "overdue items": KIND_OVERDUE,
            "late tasks report": KIND_OVERDUE,
            "give me a report": KIND_COMPLETION,
        }
        for text, kind in cases.items():
            self.assertEqual(detect_kind(text), kind, text)

    def test_formats_in_the_order_mentioned(self):
        self.assertEqual(detect_formats("a pdf and an excel copy"), ["pdf", "xlsx"])
        self.assertEqual(detect_formats("as csv"), ["csv"])
        self.assertEqual(detect_formats("with charts"), ["dashboard"])
        self.assertEqual(detect_formats("just the report"), [])
        self.assertEqual(detect_formats("export it to a spreadsheet"), ["xlsx"])


class Interpreting(unittest.TestCase):
    def go(self, text):
        return interpret(text, CTX)

    def test_the_four_example_requests(self):
        r = self.go("Generate the monthly task completion report.")
        self.assertTrue(r.understood)
        self.assertEqual((r.spec.kind, r.spec.period_id, r.fmt), (KIND_COMPLETION, "p11", "dashboard"))
        self.assertIn("P11", r.message)

        r = self.go("Prepare a report of pending tasks by team member.")
        self.assertEqual((r.spec.kind, r.spec.period_id, r.spec.member_id), (KIND_MEMBERS, "p11", None))

        r = self.go("Compare this period with the previous period.")
        self.assertEqual((r.spec.kind, r.spec.period_id, r.spec.compare_period_id), (KIND_COMPARISON, "p11", "p10"))
        self.assertIn("P11 against P10", r.message)

        r = self.go("Generate a close-status report for all entities.")
        self.assertEqual((r.spec.kind, r.spec.entity_id), (KIND_CLOSE_STATUS, None))
        self.assertIn("all entities", r.message)

    def test_format_period_and_entity(self):
        r = self.go("overdue report for DE as pdf")
        self.assertEqual((r.spec.kind, r.spec.entity_id, r.fmt), (KIND_OVERDUE, "e-de", "pdf"))
        r = self.go("completion report for P10 in excel")
        self.assertEqual((r.spec.period_id, r.fmt), ("p10", "xlsx"))
        r = self.go("generate the period report for period 9 as csv")
        self.assertEqual((r.spec.period_id, r.fmt), ("p9", "csv"))
        r = self.go("close status for Head Office")
        self.assertEqual(r.spec.entity_id, "e-hq")

    def test_a_short_entity_name_must_be_typed_as_the_name(self):
        self.assertIsNone(self.go("completion report, de rien").spec.entity_id)  # the word "de"
        self.assertEqual(self.go("completion report for NL").spec.entity_id, "e-nl")

    def test_several_formats_use_the_first_and_remember_the_rest(self):
        r = self.go("generate the overdue report as pdf and excel")
        self.assertEqual((r.fmt, r.formats), ("pdf", ["pdf", "xlsx"]))

    def test_last_period(self):
        r = self.go("completion report for last period")
        self.assertEqual(r.spec.period_id, "p10")
        r = self.go("completion report for the previous month")
        self.assertEqual(r.spec.period_id, "p10")

    def test_comparisons_name_two_periods_in_any_order(self):
        for text in ("compare P11 with P9", "compare P9 with P11", "P11 vs P9 comparison report"):
            r = self.go(text)
            self.assertEqual((r.spec.period_id, r.spec.compare_period_id), ("p11", "p9"), text)
        r = self.go("compare with the previous period")
        self.assertEqual((r.spec.period_id, r.spec.compare_period_id), ("p11", "p10"))
        r = self.go("compare P10 report")
        self.assertEqual((r.spec.period_id, r.spec.compare_period_id), ("p10", "p9"))

    def test_month_names_find_the_period_that_covers_them(self):
        r = self.go("Compare October with September")
        self.assertEqual((r.spec.period_id, r.spec.compare_period_id), ("p11", "p10"))
        r = self.go("completion report for September")
        self.assertEqual(r.spec.period_id, "p10")  # 24 of September's days fall in P10, 6 in P9
        r = self.go("completion report for Kartik")  # Nepali month, straddles P11 and P12 equally
        self.assertEqual(r.spec.period_id, "p11")  # the running period wins the tie
        r = self.go("completion report for Mangsir")
        self.assertEqual(r.spec.period_id, "p12")

    def test_a_month_with_no_period_asks(self):
        r = self.go("completion report for Magh")
        self.assertFalse(r.understood)
        self.assertIn("couldn't find a period", r.clarification)
        self.assertIn("P11", r.clarification)

    def test_an_unknown_period_asks_instead_of_guessing(self):
        r = self.go("completion report for P99")
        self.assertFalse(r.understood)
        self.assertIn("P99".lower(), r.clarification.lower())

    def test_people(self):
        r = self.go("pending tasks for Devraj")
        self.assertEqual((r.spec.kind, r.spec.member_id), (KIND_MEMBERS, "u2"))
        r = self.go("pending tasks for Binita Gopali by team member")
        self.assertEqual(r.spec.member_id, "u1")
        r = self.go("overdue report for Sisam Khatri")
        self.assertEqual((r.spec.kind, r.spec.member_id), (KIND_MEMBERS, "u4"))
        self.assertTrue(any("Sisam" in a for a in r.assumptions))

    def test_an_ambiguous_first_name_asks_which_one(self):
        r = self.go("pending tasks for Binita")
        self.assertFalse(r.understood)
        self.assertIn("Binita Gopali", r.clarification)
        self.assertIn("Binita Rai", r.clarification)
        # "Khatri" is a surname shared by two people but is not a first name: no false ambiguity
        self.assertTrue(self.go("pending tasks for Devraj").understood)

    def test_unsupported_ranges_are_flagged_not_hidden(self):
        r = self.go("quarterly completion report")
        self.assertTrue(r.understood)
        self.assertTrue(any("Quarter" in a for a in r.assumptions))

    def test_assumptions_say_what_was_defaulted(self):
        r = self.go("generate a report")
        self.assertTrue(any("current one (P11)" in a for a in r.assumptions))
        r = self.go("compare the periods")
        self.assertTrue(any("current period with the one before" in a for a in r.assumptions))

    def test_no_periods_or_no_earlier_period(self):
        r = interpret("generate a report", Context([], {}, {}, TODAY))
        self.assertFalse(r.understood)
        only = Context([PERIODS[0]], {}, {}, date(2026, 8, 20))
        r = interpret("compare this period with the previous period", only)
        self.assertFalse(r.understood)
        self.assertIn("no period before P9", r.clarification)

    def test_dict_form_is_json_ready(self):
        import json
        d = self.go("overdue report for DE as pdf").to_dict()
        json.dumps(d)
        self.assertEqual(d["spec"]["kind"], KIND_OVERDUE)
        self.assertEqual(d["format"], "pdf")


if __name__ == "__main__":
    unittest.main()
