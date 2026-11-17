import unittest

from app.services import timesheet_matching as tm
from app.services.timesheet_matching import (
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    Activity,
    SubActivity,
    WorkText,
    decide,
    name_key,
)

# The activity list the real HRMS returned when this was written: placeholder
# data. Real finance tasks must never be auto-matched onto it.
REAL_CATALOG = [
    Activity(31, "Activity 2", None),
    Activity(32, "Another Activity", None),
    Activity(33, "Have Sub Activity", "asdfasdf", (
        SubActivity(60, "First sub activity"), SubActivity(66, "Second"),
        SubActivity(67, "Third"), SubActivity(68, "Fourth"), SubActivity(72, "test"),
    )),
    Activity(34, "Testing it", "asdfadsf"),
    Activity(36, "test 2", "Just for test with activities", (SubActivity(69, "Description"),)),
    Activity(37, "Innovation Next Project", "Actiivities for the innovation next"),
    Activity(38, "Sewa App Project", None),
]

FINANCE = [
    Activity(1, "Bank Reconciliation", "Match the bank statement to the ledger"),
    Activity(2, "VAT Return Filing", None),
    Activity(3, "Payroll Processing", None),
    Activity(4, "Client Meeting", None),
    Activity(5, "Development", None),
    Activity(6, "Month End Close", None, (
        SubActivity(10, "Accruals Posting"), SubActivity(11, "Fixed Asset Depreciation"),
    )),
]


def w(title, description=None, **kw):
    return WorkText(title=title, description=description, **kw)


class NormalizeTests(unittest.TestCase):
    def test_strips_markup_punctuation_case(self):
        self.assertEqual(tm.normalize("<p>Bank  RECON.,</p>"), "bank recon")

    def test_plural_and_number_handling(self):
        self.assertEqual(tm.tokens("Invoices"), {"invoice"})
        self.assertEqual(tm.tokens("reviews reviewing reviewed review"), {"review"})
        self.assertEqual(tm.tokens("need feed bed process"), {"need", "feed", "bed", "process"})   # short words untouched
        self.assertEqual(tm.distinctive({"monthly", "report", "2", "ledger"}), {"ledger"})

    def test_name_key_ignores_order_and_filler(self):
        self.assertEqual(name_key("Bank Reconciliation of the month"),
                         name_key("month Bank reconciliation"))


class TextMatchTests(unittest.TestCase):
    def test_title_contains_full_activity_name_is_auto_matched(self):
        d = decide(w("Bank Reconciliation - October"), FINANCE)
        self.assertEqual((d.status, d.activity_id, d.tier), (MATCHED, 1, tm.TEXT))

    def test_a_date_or_period_on_the_end_of_the_title_still_matches(self):
        acts = [Activity(1, "Dashboard review"), Activity(2, "Bank Reconciliation"), Activity(3, "Client meeting")]
        for title in ("Dashboard review", "Dashboard review — Oct 28", "Dashboard review - October 2026",
                      "Q3 Dashboard Review (HQ)", "dashboard reviews"):
            d = decide(w(title), acts)
            self.assertEqual((d.status, d.activity_id), (MATCHED, 1), title)

    def test_word_order_filler_plurals_and_verb_forms_do_not_matter(self):
        acts = [Activity(1, "Dashboard review"), Activity(2, "Client meeting")]
        for title in ("Review dashboard", "review DASHBOARD - Oct 28", "Review of the dashboard",
                      "Dashboards review", "Dashboard reviews", "Reviewing dashboard", "Reviewed the dashboards",
                      "Dashboard reviewing - Q3"):
            d = decide(w(title), acts)
            self.assertEqual((d.status, d.activity_id), (MATCHED, 1), title)
        d = decide(w("Client meetings with Himalayan Traders"), acts)
        self.assertEqual((d.status, d.activity_id), (MATCHED, 2))

    def test_a_different_word_is_not_treated_as_the_same(self):
        d = decide(w("Dashboard check"), [Activity(1, "Dashboard review")])
        self.assertNotEqual(d.status, MATCHED)

    def test_only_part_of_the_activity_name_is_not_enough(self):
        acts = [Activity(1, "Dashboard review"), Activity(2, "Code review")]
        d = decide(w("Review invoices for October"), acts)
        self.assertNotEqual(d.status, MATCHED)
        d = decide(w("Dashboard design"), acts)
        self.assertNotEqual(d.status, MATCHED)

    def test_names_made_only_of_everyday_words_never_match_by_wording(self):
        for name in ("Monthly report", "Review", "Final update"):
            d = decide(w(f"{name} for Himalayan Traders — Oct 28"), [Activity(1, name)])
            self.assertNotEqual(d.status, MATCHED, name)

    def test_a_single_candidate_is_described_honestly(self):
        d = decide(w("Development of reporting module"), FINANCE)
        self.assertEqual(d.status, AMBIGUOUS)
        self.assertIn("possibly 'Development'", d.reason)
        self.assertNotIn("more than one", d.reason)

    def test_exact_name_is_confident(self):
        d = decide(w("VAT return filing"), FINANCE)
        self.assertEqual((d.status, d.activity_id), (MATCHED, 2))
        self.assertEqual(d.score, 1.0)

    def test_typo_is_tolerated(self):
        d = decide(w("Bank reconcilation"), FINANCE)
        self.assertEqual((d.status, d.activity_id), (MATCHED, 1))

    def test_single_word_activity_is_never_auto_matched(self):
        d = decide(w("Development of reporting module"), FINANCE)
        self.assertNotEqual(d.status, MATCHED)
        self.assertEqual(d.suggestions[0].activity_id, 5)

    def test_unrelated_task_is_unmatched(self):
        d = decide(w("Prepare board presentation"), FINANCE)
        self.assertEqual(d.status, UNMATCHED)
        self.assertIsNone(d.activity_id)

    def test_two_equally_good_activities_are_ambiguous(self):
        acts = [Activity(1, "Audit Support Work"), Activity(2, "Audit Support Tasks")]
        d = decide(w("Audit support file prep"), acts)
        self.assertEqual(d.status, AMBIGUOUS)
        self.assertIsNone(d.activity_id)
        self.assertEqual(len(d.suggestions), 2)

    def test_generic_words_alone_never_match(self):
        acts = [Activity(1, "Monthly Report")]
        d = decide(w("Monthly report for client X"), acts)
        self.assertNotEqual(d.status, MATCHED)

    def test_name_in_description_is_only_a_suggestion(self):
        d = decide(w("Quarter work", "do the vat return filing"), FINANCE)
        self.assertNotEqual(d.status, MATCHED)

    def test_real_placeholder_catalog_never_auto_matches_finance_work(self):
        for title in ("Bank Reconciliation", "VAT return filing", "Testing the ledger",
                      "Activity report", "Second review", "Unit testing", "Another task"):
            d = decide(w(title), REAL_CATALOG)
            self.assertNotEqual(d.status, MATCHED, title)

    def test_innovation_next_project_work_does_match_its_activity(self):
        d = decide(w("Innovation Next project setup"), REAL_CATALOG)
        self.assertEqual((d.status, d.activity_id), (MATCHED, 37))


class SubActivityTests(unittest.TestCase):
    """Sub activities are extra FIELDS the HRMS wants values for, not alternatives:
    the matcher picks the activity only (the builder deals with the fields)."""

    def test_an_activity_with_fields_matches_on_its_own_name(self):
        d = decide(w("Month end close October"), FINANCE)
        self.assertEqual((d.status, d.activity_id), (MATCHED, 6))

    def test_field_names_are_not_used_to_pick_the_activity(self):
        # "Accruals Posting" / "Fixed Asset Depreciation" are fields of Month End Close.
        d = decide(w("Fixed asset depreciation run"), FINANCE)
        self.assertNotEqual((d.status, d.activity_id), (MATCHED, 6))

    def test_decisions_carry_no_sub_activity(self):
        d = decide(w("Month end close October"), FINANCE)
        self.assertFalse(hasattr(d, "sub_activity_id"))


class MappingTests(unittest.TestCase):
    def test_template_mapping_beats_wording(self):
        maps = {("template", "tpl-1"): 4}
        d = decide(w("Bank Reconciliation", template_id="tpl-1"), FINANCE, maps)
        self.assertEqual((d.status, d.activity_id, d.tier), (MATCHED, 4, tm.MAPPING_TEMPLATE))

    def test_title_mapping_for_custom_tasks(self):
        maps = {("title", name_key("Weekly sync")): 4}
        d = decide(w("weekly SYNC"), FINANCE, maps)
        self.assertEqual((d.activity_id, d.tier), (4, tm.MAPPING_TITLE))

    def test_subtask_inherits_parent_mapping(self):
        maps = {("template", "parent-tpl"): 2}
        d = decide(w("Collect invoices", parent_template_id="parent-tpl"), FINANCE, maps)
        self.assertEqual((d.status, d.activity_id, d.tier), (MATCHED, 2, tm.MAPPING_PARENT))

    def test_own_mapping_beats_parent_mapping(self):
        maps = {("template", "own"): 3, ("template", "parent"): 2}
        d = decide(w("x", template_id="own", parent_template_id="parent"), FINANCE, maps)
        self.assertEqual(d.activity_id, 3)

    def test_mapping_to_deleted_activity_is_ignored(self):
        maps = {("template", "tpl"): 999}
        d = decide(w("Prepare board presentation", template_id="tpl"), FINANCE, maps)
        self.assertEqual(d.status, UNMATCHED)

    def test_mapping_from_another_projects_activity_list_is_ignored(self):
        # The caller passes only the selected project's activities; a mapping
        # pointing outside that list must not resurrect an activity.
        only_two = [a for a in FINANCE if a.id in (1, 2)]
        maps = {("template", "tpl"): 4}
        d = decide(w("Prepare board presentation", template_id="tpl"), only_two, maps)
        self.assertEqual(d.status, UNMATCHED)


if __name__ == "__main__":
    unittest.main()
