"""A task's due date never leaves its own period.

A Kartik task is never due in Mangsir; a date in another period shows up only in
that period. Covers the period-bounded N-Day Due Date, the retired "N days after
period end" option, and the schema backstop for old clients / bulk imports.
"""

import unittest
from datetime import date
from types import SimpleNamespace

from app.calendar import set_configured_holidays
from app.models import DueAnchor, DueDateType
from app.schemas import SubTaskTemplateIn, SubTaskUpdate, TaskCreate, TaskTemplateIn, TaskUpdate
from app.services.due_date_rules import (
    DueDateOutOfRangeError,
    apply_due_days,
    last_working_day,
    resolve_generated_due_date,
    validate_due_date,
)
from app.services.plan_generation_service import preview_tasks_for_period

# Kartik 2083: Sun Oct 18 -> Mon Nov 16 2026. Sat Oct 31 / Sun Nov 1 sit mid-period.
KARTIK = (date(2026, 10, 18), date(2026, 11, 16))
# A period that ends on a Sunday (Oct 5 -> Sun Nov 1), like the weekend cleanup hit.
ENDS_SUNDAY = (date(2026, 10, 5), date(2026, 11, 1))


def _period(start, end):
    return SimpleNamespace(start_date=start, end_date=end)


def _tmpl(**kw):
    base = dict(title="T", interval_count=1, days_of_week=None, due_days=0, sub_task_templates=[])
    base.update(kw)
    return base


class LastWorkingDayTests(unittest.TestCase):
    def tearDown(self):
        set_configured_holidays({})

    def test_a_working_day_period_end_is_itself(self):
        self.assertEqual(last_working_day(*KARTIK), date(2026, 11, 16))  # Monday

    def test_a_weekend_period_end_goes_back_to_friday(self):
        self.assertEqual(last_working_day(*ENDS_SUNDAY), date(2026, 10, 30))

    def test_a_holiday_on_the_last_working_day_goes_back_again(self):
        set_configured_holidays({date(2026, 10, 30): "Holiday"})
        self.assertEqual(last_working_day(*ENDS_SUNDAY), date(2026, 10, 29))


class ApplyDueDaysInsideThePeriodTests(unittest.TestCase):
    def test_unbounded_call_still_rolls_forward_like_before(self):
        # no period given (a bare "next occurrence" pointer): Sun Nov 1 -> Mon Nov 2
        self.assertEqual(apply_due_days(date(2026, 11, 1), 0), date(2026, 11, 2))

    def test_a_sunday_on_the_last_day_of_the_period_is_due_the_friday_before(self):
        due = apply_due_days(
            date(2026, 11, 1), 0, period_start=ENDS_SUNDAY[0], period_end=ENDS_SUNDAY[1],
        )
        self.assertEqual(due, date(2026, 10, 30))

    def test_a_mid_period_weekend_still_rolls_forward_to_monday(self):
        due = apply_due_days(date(2026, 10, 31), 0, period_start=KARTIK[0], period_end=KARTIK[1])
        self.assertEqual(due, date(2026, 11, 2))  # Mon Nov 2 is inside Kartik

    def test_due_days_that_overshoot_the_period_end_are_clamped(self):
        # Nov 14 + 5 days = Nov 19 (Mangsir) -> Kartik's last day, Mon Nov 16
        due = apply_due_days(date(2026, 11, 14), 5, period_start=KARTIK[0], period_end=KARTIK[1])
        self.assertEqual(due, date(2026, 11, 16))

    def test_never_leaves_the_period_for_any_occurrence_or_due_days(self):
        start, end = KARTIK
        d = start
        while d <= end:
            for n in (0, 1, 3, 5, 10, 40):
                due = apply_due_days(d, n, period_start=start, period_end=end)
                self.assertTrue(start <= due <= end, (d, n, due))
                self.assertLess(due.weekday(), 5, (d, n, due))
            d = date.fromordinal(d.toordinal() + 1)


class AfterPeriodEndIsRetiredTests(unittest.TestCase):
    def test_a_legacy_template_resolves_to_the_last_working_day_of_its_period(self):
        due = resolve_generated_due_date(
            *KARTIK, 1, "PERIOD_START", DueDateType.AFTER_PERIOD_END, 5,
        )
        self.assertEqual(due, date(2026, 11, 16))  # not Nov 21, which is Mangsir

    def test_a_date_in_the_next_period_is_rejected(self):
        with self.assertRaises(DueDateOutOfRangeError):
            validate_due_date(
                period_start=KARTIK[0], period_end=KARTIK[1], due_date=date(2026, 11, 21),
                due_date_type=DueDateType.AFTER_PERIOD_END, post_period_due_days=5,
            )

    def test_preview_of_a_once_per_period_after_period_end_template_stays_in_the_period(self):
        rows = preview_tasks_for_period(
            [_tmpl(due_day_of_period=1, due_anchor="PERIOD_START",
                   due_date_type="AFTER_PERIOD_END", post_period_due_days=5)],
            _period(*KARTIK),
        )
        self.assertEqual(rows[0]["due_date"], date(2026, 11, 16))
        self.assertEqual(rows[0]["due_date_type"], "WITHIN_PERIOD")
        self.assertEqual(rows[0]["post_period_due_days"], 0)


class RecurringDueDatesInsidePreviewTests(unittest.TestCase):
    def test_monthly_occurrence_on_the_last_sunday_is_due_before_the_period_ends(self):
        # Nov 1 is the last day of this period; Monday Nov 2 belongs to the next one.
        rows = preview_tasks_for_period(
            [_tmpl(frequency="MONTHLY", recurrence_start=date(2026, 11, 1))],
            _period(*ENDS_SUNDAY),
        )
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 11, 1))
        self.assertEqual(rows[0]["due_date"], date(2026, 10, 30))

    def test_every_daily_row_in_a_period_is_due_inside_it(self):
        rows = preview_tasks_for_period(
            [_tmpl(frequency="DAILY", recurrence_start=KARTIK[0], due_days=7)], _period(*KARTIK),
        )
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(KARTIK[0] <= r["due_date"] <= KARTIK[1], r)
            self.assertLess(r["due_date"].weekday(), 5, r)

    def test_a_placeholder_still_names_the_next_occurrence_in_the_later_period(self):
        # No quarterly occurrence lands in November; the row says when the next one does.
        rows = preview_tasks_for_period(
            [_tmpl(frequency="QUARTERLY", recurrence_start=date(2026, 10, 19))], _period(*KARTIK),
        )
        self.assertTrue(rows[0]["occurs_in_period"])  # Oct 19 is inside Kartik
        rows = preview_tasks_for_period(
            [_tmpl(frequency="QUARTERLY", recurrence_start=date(2026, 10, 19))],
            _period(date(2026, 11, 17), date(2026, 12, 15)),
        )
        self.assertFalse(rows[0]["occurs_in_period"])
        self.assertEqual(rows[0]["next_due_date"], date(2027, 1, 19))


class RetiredFieldsAreCoercedOnInputTests(unittest.TestCase):
    def test_a_task_template_marked_after_period_end_becomes_the_last_day(self):
        t = TaskTemplateIn(
            title="x", due_day_of_period=3, due_anchor=DueAnchor.PERIOD_START,
            due_date_type="AFTER_PERIOD_END", post_period_due_days=5,
        )
        self.assertEqual(t.due_date_type, DueDateType.WITHIN_PERIOD)
        self.assertEqual(t.post_period_due_days, 0)
        self.assertEqual((t.due_anchor, t.due_day_of_period), (DueAnchor.PERIOD_END, 1))

    def test_a_within_period_template_keeps_its_own_day(self):
        t = TaskTemplateIn(title="x", due_day_of_period=3, due_anchor=DueAnchor.PERIOD_START)
        self.assertEqual((t.due_anchor, t.due_day_of_period), (DueAnchor.PERIOD_START, 3))

    def test_a_sub_task_template_marked_after_period_end_becomes_the_last_day(self):
        s = SubTaskTemplateIn(
            title="x", due_day_of_period=2, due_date_type="AFTER_PERIOD_END", post_period_due_days=4,
        )
        self.assertEqual(s.due_date_type, DueDateType.WITHIN_PERIOD)
        self.assertEqual((s.due_anchor, s.due_day_of_period, s.post_period_due_days), (DueAnchor.PERIOD_END, 1, 0))

    def test_create_and_update_payloads_are_coerced(self):
        c = TaskCreate(plan_id="p", title="x", due_date_type="AFTER_PERIOD_END", post_period_due_days=9)
        self.assertEqual((c.due_date_type, c.post_period_due_days), (DueDateType.WITHIN_PERIOD, 0))
        for model in (TaskUpdate, SubTaskUpdate):
            u = model(due_date_type="AFTER_PERIOD_END", post_period_due_days=9)
            self.assertEqual((u.due_date_type, u.post_period_due_days), (DueDateType.WITHIN_PERIOD, 0))
            untouched = model()
            self.assertEqual((untouched.due_date_type, untouched.post_period_due_days), (None, None))


if __name__ == "__main__":
    unittest.main()
