import random
import unittest
from datetime import date

from app.services import timesheet_builder as tb
from app.services.hrms_timesheet_gateway import ExistingEntry
from app.services.timesheet_builder import (
    ALREADY_IN_HRMS, ALREADY_SENT, DAY_OVER_24H, OTHER_HRMS_ENTRIES, SUMMARY_CLIPPED,
    Chosen, Item, Work, build_lines, build_summary, hours_from_minutes,
)
from app.services.timesheet_matching import (
    MATCHED, NEEDS_SUB, UNMATCHED, Activity, SubActivity,
)

ACTS = [
    Activity(1, "Bank Reconciliation"),
    Activity(2, "VAT Return Filing"),
    # an activity whose HRMS entry needs a value for each of its extra fields
    Activity(6, "Month End Close", None, (SubActivity(10, "Accruals Posting"), SubActivity(11, "Review level"))),
    # ... and one whose only field is a description (filled from the summary)
    Activity(7, "Client Call", None, (SubActivity(20, "Description", None, "description", 1),)),
]
D1, D2 = date(2026, 10, 5), date(2026, 10, 6)


def task(id_, title, desc=None, tpl=None):
    return Item(id_, "task", title, desc, tpl)


def sub(id_, title, parent, desc=None, tpl=None):
    return Item(id_, "subtask", title, desc, tpl, parent)


def work(unit_id, day, minutes=60):
    return Work(unit_id, day, minutes)


def items_of(*xs):
    return {i.id: i for i in xs}


class HoursTests(unittest.TestCase):
    def test_rounding_half_up_two_decimals(self):
        self.assertEqual(hours_from_minutes(50), 0.83)
        self.assertEqual(hours_from_minutes(45), 0.75)
        self.assertEqual(hours_from_minutes(1), 0.02)   # 0.0166...
        self.assertEqual(hours_from_minutes(90), 1.5)


class RowTests(unittest.TestCase):
    """One row per submitted task / sub task."""

    def test_a_submission_with_time_taken_is_a_ready_row(self):
        items = items_of(task("a", "Bank Reconciliation", "Matched the HQ account"))
        (line,) = build_lines([work("a", D1, 90)], items, ACTS)
        self.assertEqual((line.status, line.activity_id, line.minutes, line.hours), (MATCHED, 1, 90, 1.5))
        self.assertEqual((line.needs_hours, line.selected), (False, True))
        self.assertEqual(line.summary, "Matched the HQ account")
        self.assertEqual([(s.unit_kind, s.unit_id, s.minutes) for s in line.sources], [("task", "a", 90)])

    def test_each_submission_is_its_own_row_and_none_is_merged_in_the_preview(self):
        items = items_of(task("a", "Bank Reconciliation - HQ"), task("b", "Bank Reconciliation - Branch"))
        lines = build_lines([work("a", D1, 20), work("b", D1, 25)], items, ACTS)
        self.assertEqual(sorted((l.activity_id, l.minutes) for l in lines), [(1, 20), (1, 25)])

    def test_blank_time_taken_leaves_empty_hours_to_type_and_is_never_preselected(self):
        items = items_of(task("a", "Bank Reconciliation"))
        (line,) = build_lines([work("a", D1, 0)], items, ACTS)
        self.assertEqual((line.needs_hours, line.minutes, line.selected, line.status), (True, 0, False, MATCHED))

    def test_different_days_are_different_rows_in_date_order(self):
        items = items_of(task("a", "Bank Reconciliation"), task("b", "VAT return filing"))
        lines = build_lines([work("b", D2), work("a", D1)], items, ACTS)
        self.assertEqual([(l.work_date, l.activity_id) for l in lines], [(D1, 1), (D2, 2)])

    def test_unmatched_work_is_flagged_and_not_selected(self):
        items = items_of(task("x", "Prepare board presentation"), task("a", "Bank Reconciliation"))
        lines = {l.status: l for l in build_lines([work("x", D1, 40), work("a", D1, 20)], items, ACTS)}
        self.assertEqual((lines[UNMATCHED].minutes, lines[UNMATCHED].selected, lines[UNMATCHED].activity_id), (40, False, None))
        self.assertTrue(lines[MATCHED].selected)

    def test_a_sub_task_row_names_its_parent_and_points_at_the_sub_task(self):
        items = items_of(task("p", "October close"), sub("s", "Bank Reconciliation", "p", "Clear the open items"))
        (line,) = build_lines([work("s", D2, 30)], items, ACTS)
        self.assertEqual(line.summary, "October close: Bank Reconciliation – Clear the open items")
        src = line.sources[0]
        self.assertEqual((src.unit_kind, src.unit_id, src.task_title, src.subtask_title), ("subtask", "s", "October close", "Bank Reconciliation"))

    def test_sub_tasks_of_one_task_can_land_on_different_activities(self):
        items = items_of(task("p", "October close"), sub("s1", "Bank Reconciliation", "p"), sub("s2", "VAT return filing", "p"))
        lines = build_lines([work("s1", D1, 30), work("s2", D1, 45)], items, ACTS)
        self.assertEqual({(l.activity_id, l.minutes) for l in lines}, {(1, 30), (2, 45)})

    def test_a_sub_task_inherits_its_parents_remembered_activity(self):
        items = items_of(task("p", "Close", tpl="tp"), sub("s", "Collect statements", "p"))
        (line,) = build_lines([work("s", D1, 30)], items, ACTS, {("template", "tp"): 2})
        self.assertEqual((line.status, line.activity_id), (MATCHED, 2))

    def test_work_on_a_task_that_no_longer_exists_is_simply_not_shown(self):
        self.assertEqual(build_lines([work("ghost", D1, 15)], {}, ACTS), [])

    def test_a_day_over_24_hours_is_flagged_and_held(self):
        items = items_of(task("a", "Bank Reconciliation"), task("b", "VAT return filing"))
        lines = build_lines([work("a", D1, 800), work("b", D1, 700)], items, ACTS)
        self.assertTrue(all(DAY_OVER_24H in l.flags and not l.selected for l in lines))

    def test_keys_are_stable_and_distinct_whatever_the_order(self):
        items = items_of(task("a", "Bank Reconciliation"), task("b", "VAT return filing"))
        one = build_lines([work("a", D1), work("b", D1)], items, ACTS)
        two = build_lines([work("b", D1), work("a", D1)], items, ACTS)
        self.assertEqual(sorted(l.key for l in one), sorted(l.key for l in two))
        self.assertEqual(len({l.key for l in one}), 2)

    def test_remembered_choices_are_looked_up_for_the_work(self):
        items = items_of(task("a", "Anything at all", tpl="tp"))
        self.assertEqual([t.template_id for t in tb.work_texts([work("a", D1)], items)], ["tp"])
        (line,) = build_lines([work("a", D1)], items, ACTS, {("template", "tp"): 2})
        self.assertEqual((line.status, line.activity_id), (MATCHED, 2))


class EveryRowIsKeptOnceProperty(unittest.TestCase):
    def test_every_submission_becomes_exactly_one_row_for_random_data(self):
        rng = random.Random(7)
        titles = ["Bank Reconciliation", "VAT return filing", "Month end close",
                  "Prepare board presentation", "Payroll run", "Audit support"]
        for trial in range(60):
            items, ids = {}, []
            for i in range(rng.randint(1, 8)):
                t = task(f"t{trial}_{i}", rng.choice(titles) + f" {rng.randint(1, 9)}")
                items[t.id] = t
                ids.append(t.id)
                for j in range(rng.randint(0, 3)):
                    s = sub(f"s{trial}_{i}_{j}", rng.choice(titles), t.id)
                    items[s.id] = s
                    ids.append(s.id)
            wk = [work(u, date(2026, 10, rng.randint(1, 28)), rng.choice([0, 15, 60, 240])) for u in ids]
            lines = build_lines(wk, items, ACTS)
            self.assertEqual(len(lines), len(wk))
            self.assertEqual(sum(l.minutes for l in lines), sum(w.minutes for w in wk))
            self.assertEqual(sorted(s.unit_id for l in lines for s in l.sources), sorted(ids))   # each once
            shuffled = wk[:]
            rng.shuffle(shuffled)
            again = build_lines(shuffled, items, ACTS)
            self.assertEqual(sorted((l.key, l.minutes, l.activity_id) for l in lines),
                             sorted((l.key, l.minutes, l.activity_id) for l in again))


class SummaryTests(unittest.TestCase):
    def test_without_subtasks_uses_task_description(self):
        s, clipped = build_summary([(task("t", "Bank rec", "<p>Match the <b>statement</b></p>"), None)], 1000)
        self.assertEqual((s, clipped), ("Match the statement", False))

    def test_task_without_description_falls_back_to_title(self):
        self.assertEqual(build_summary([(task("t", "Bank rec"), None)], 1000)[0], "Bank rec")

    def test_subtasks_are_listed_with_names_and_descriptions(self):
        p = task("p", "October close", "ignored parent text")
        s1, s2 = sub("s1", "Post accruals", "p", "Rent and utilities"), sub("s2", "Review TB", "p")
        text, _ = build_summary([(s2, p), (s1, p)], 1000)
        self.assertEqual(text, "October close: Post accruals – Rent and utilities; Review TB")

    def test_only_subtasks_that_were_passed_in_are_named(self):
        p = task("p", "October close")
        self.assertNotIn("Review TB", build_summary([(sub("s1", "Post accruals", "p"), p)], 1000)[0])

    def test_parent_level_work_uses_parent_description_even_with_subtasks(self):
        p = task("p", "October close", "Overall close work")
        self.assertEqual(build_summary([(p, None)], 1000)[0], "Overall close work")

    def test_two_tasks_are_joined_and_repeats_dropped(self):
        a, b = task("a", "A", "Same text"), task("b", "B", "Same text")
        c = task("c", "C", "Other text")
        self.assertEqual(build_summary([(a, None), (b, None), (c, None)], 1000)[0], "Same text | Other text")

    def test_long_summary_is_shortened_and_flagged_not_silently_cut(self):
        text, clipped = build_summary([(task("t", "T", "word " * 400), None)], 100)
        self.assertTrue(clipped)
        self.assertLessEqual(len(text), 100)
        self.assertTrue(text.endswith("…"))
        items = items_of(task("a", "Bank Reconciliation", "word " * 400))
        (line,) = build_lines([work("a", D1, 10)], items, ACTS, max_summary_chars=100)
        self.assertIn(SUMMARY_CLIPPED, line.flags)


class ExistingEntryTests(unittest.TestCase):
    def _lines(self):
        return build_lines([work("a", D1, 60)], items_of(task("a", "Bank Reconciliation")), ACTS)

    def test_same_day_activity_on_hrms_is_flagged_and_deselected(self):
        lines = self._lines()
        tb.apply_existing(lines, [ExistingEntry(D1, 1, 2.0)])
        self.assertIn(ALREADY_IN_HRMS, lines[0].flags)
        self.assertFalse(lines[0].selected)
        self.assertEqual(lines[0].existing_hours, 2.0)

    def test_other_activity_same_day_is_only_a_notice(self):
        lines = self._lines()
        tb.apply_existing(lines, [ExistingEntry(D1, 2, 3.0)])
        self.assertIn(OTHER_HRMS_ENTRIES, lines[0].flags)
        self.assertTrue(lines[0].selected)

    def test_already_sent_from_closeloop_is_flagged(self):
        lines = self._lines()
        tb.apply_existing(lines, [], [ExistingEntry(D1, 1, 1.0)])
        self.assertIn(ALREADY_SENT, lines[0].flags)
        self.assertFalse(lines[0].selected)

    def test_other_day_does_not_matter(self):
        lines = self._lines()
        tb.apply_existing(lines, [ExistingEntry(D2, 1, 8.0)])
        self.assertEqual(lines[0].flags, [])
        self.assertTrue(lines[0].selected)

    def test_hrms_day_plus_closeloop_over_24h_is_held(self):
        lines = self._lines()
        tb.apply_existing(lines, [ExistingEntry(D1, 2, 23.5)])
        self.assertIn(DAY_OVER_24H, lines[0].flags)
        self.assertFalse(lines[0].selected)

    def test_a_day_already_sent_from_closeloop_is_closed_to_every_activity(self):
        # The HRMS takes a whole day in one request, so sending the date again is unsafe.
        items = items_of(task("a", "Bank Reconciliation"), task("b", "VAT return filing"))
        lines = build_lines([work("a", D1, 60), work("b", D1, 30)], items, ACTS)
        tb.apply_existing(lines, [], [ExistingEntry(D1, 3, 1.0)])      # sent earlier: a different activity
        self.assertTrue(all(tb.DAY_ALREADY_SENT in l.flags for l in lines))
        self.assertFalse(any(l.selected for l in lines))
        t = tb.totals(lines)
        self.assertEqual((t["already_in_hrms_minutes"], t["ready_minutes"]), (90, 0))

    def test_other_dates_are_not_affected_by_a_sent_day(self):
        lines = build_lines([work("a", D2, 60)], items_of(task("a", "Bank Reconciliation")), ACTS)
        tb.apply_existing(lines, [], [ExistingEntry(D1, 1, 1.0)])
        self.assertEqual(lines[0].flags, [])
        self.assertTrue(lines[0].selected)

    def test_a_row_with_no_hours_yet_is_closed_by_a_sent_day_too(self):
        lines = build_lines([work("a", D1, 0)], items_of(task("a", "Bank Reconciliation")), ACTS)
        tb.apply_existing(lines, [], [ExistingEntry(D1, 9, 1.0)])
        self.assertIn(tb.DAY_ALREADY_SENT, lines[0].flags)


class SubActivityFieldTests(unittest.TestCase):
    """Sub activities are fields the HRMS wants a VALUE for, not alternatives."""

    def test_a_description_field_is_filled_from_the_summary_and_the_row_is_ready(self):
        items = items_of(task("c", "Client Call", "Quarterly review call with the client"))
        (line,) = build_lines([work("c", D1, 30)], items, ACTS)
        self.assertEqual((line.status, line.activity_id, line.selected), (MATCHED, 7, True))
        self.assertEqual(line.sub_values, {20: "Quarterly review call with the client"})

    def test_other_fields_stay_empty_so_the_row_waits_for_the_user(self):
        items = items_of(task("m", "Month end close October"))
        (line,) = build_lines([work("m", D1, 60)], items, ACTS)
        self.assertEqual((line.status, line.activity_id, line.selected), (NEEDS_SUB, 6, False))
        self.assertEqual(line.sub_values, {10: "", 11: ""})
        self.assertIn("Accruals Posting", line.reason)
        self.assertIn("Review level", line.reason)

    def test_an_activity_without_fields_has_none(self):
        (line,) = build_lines([work("a", D1, 30)], items_of(task("a", "Bank Reconciliation")), ACTS)
        self.assertEqual((line.sub_values, line.status), ({}, MATCHED))

    def test_values_complete_needs_every_field_non_blank(self):
        act = ACTS[2]
        self.assertFalse(tb.values_complete(act, {}))
        self.assertFalse(tb.values_complete(act, {10: "x", 11: "  "}))
        self.assertTrue(tb.values_complete(act, {10: "x", 11: "y"}))
        self.assertTrue(tb.values_complete(ACTS[0], {}))       # no fields -> trivially complete
        self.assertFalse(tb.values_complete(None, {}))

    def test_prefill_recognises_description_like_fields_by_name_or_key(self):
        act = Activity(9, "X", None, (SubActivity(1, "Details"), SubActivity(2, "Field", None, "remarks"),
                                      SubActivity(3, "Hours code")))
        self.assertEqual(tb.prefill_sub_values(act, "did the thing"), {1: "did the thing", 2: "did the thing", 3: ""})

    def test_extra_fields_hold_back_a_row_with_no_hours_too(self):
        (line,) = build_lines([work("m", D1, 0)], items_of(task("m", "Month end close October")), ACTS)
        self.assertEqual((line.status, line.needs_hours, line.selected), (NEEDS_SUB, True, False))


class TotalsTests(unittest.TestCase):
    def test_buckets_add_up(self):
        items = items_of(task("a", "Bank Reconciliation"), task("b", "VAT return filing"),
                         task("x", "Prepare board presentation"), task("n", "Payroll run"))
        lines = build_lines([work("a", D1, 60), work("b", D1, 30), work("x", D1, 20), work("n", D1, 0)], items, ACTS)
        tb.apply_existing(lines, [ExistingEntry(D1, 2, 1.0)])
        t = tb.totals(lines, filed_tasks=3, filed_minutes=100)
        self.assertEqual((t["tasks"], t["reported_minutes"], t["hours_missing"]), (4, 110, 1))
        self.assertEqual((t["ready_minutes"], t["already_in_hrms_minutes"], t["needs_review_minutes"]), (60, 30, 20))
        self.assertEqual((t["selected_minutes"], t["already_filed_tasks"], t["already_filed_minutes"]), (60, 3, 100))

    def test_every_bucket_is_a_partition_of_the_reported_minutes(self):
        items = items_of(task("a", "Bank Reconciliation"), task("b", "VAT return filing"), task("x", "Prepare board presentation"))
        lines = build_lines([work("a", D1, 700), work("b", D1, 800), work("x", D2, 20)], items, ACTS)
        t = tb.totals(lines)
        self.assertEqual(t["ready_minutes"] + t["already_in_hrms_minutes"] + t["needs_review_minutes"] + t["held_minutes"],
                         t["reported_minutes"])


class MergeTests(unittest.TestCase):
    def _c(self, minutes, summary, act=1, work_type="BILLABLE", day=D1, values=None):
        return Chosen(day, act, minutes, summary, work_type, [], dict(values or {}))

    def test_same_activity_same_day_merges_with_one_rounding(self):
        merged = tb.merge_chosen([self._c(10, "a"), self._c(10, "b"), self._c(10, "a")], 1000)
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0].minutes, merged[0].hours, merged[0].summary), (30, 0.5, "a | b"))

    def test_different_days_or_activities_stay_apart(self):
        merged = tb.merge_chosen([self._c(10, "a"), self._c(10, "a", day=D2), self._c(10, "a", act=2)], 1000)
        self.assertEqual(len(merged), 3)

    def test_different_work_types_on_one_activity_and_day_are_a_conflict_not_a_silent_pick(self):
        with self.assertRaises(tb.MergeConflict) as cm:
            tb.merge_chosen([self._c(10, "a"), self._c(10, "a", work_type="NON_BILLABLE")], 1000)
        self.assertEqual(cm.exception.code, "WORK_TYPE_CONFLICT")

    def test_field_values_merge_when_they_agree_or_one_is_blank(self):
        merged = tb.merge_chosen([self._c(10, "a", act=6, values={10: "A", 11: ""}),
                                  self._c(10, "b", act=6, values={10: "A", 11: "high"})], 1000)
        self.assertEqual(merged[0].sub_values, {10: "A", 11: "high"})

    def test_field_values_that_disagree_are_a_conflict(self):
        with self.assertRaises(tb.MergeConflict) as cm:
            tb.merge_chosen([self._c(10, "a", act=6, values={10: "A"}), self._c(10, "b", act=6, values={10: "B"})], 1000)
        self.assertEqual(cm.exception.code, "VALUES_CONFLICT")


class FindDuplicatesTests(unittest.TestCase):
    def test_same_date_and_activity_on_the_hrms_or_sent_here(self):
        c = Chosen(D1, 1, 60, "x", "BILLABLE", [])
        self.assertEqual(tb.find_duplicates([c], [ExistingEntry(D1, 1, 1.0)]), [c])
        self.assertEqual(tb.find_duplicates([c], [], [ExistingEntry(D1, 1, 1.0)]), [c])
        self.assertEqual(tb.find_duplicates([c], [ExistingEntry(D1, 2, 1.0)]), [])

    def test_any_activity_on_a_day_already_sent_is_a_duplicate(self):
        c = Chosen(D1, 1, 60, "x", "BILLABLE", [])
        self.assertEqual(tb.find_duplicates([c], [], [ExistingEntry(D1, 9, 1.0)]), [c])
        self.assertEqual(tb.find_duplicates([c], [], [ExistingEntry(D2, 9, 1.0)]), [])


if __name__ == "__main__":
    unittest.main()
