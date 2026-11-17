import unittest
from datetime import date, timedelta

from app.calendar import (
    NEPAL_WEEKEND,
    classify_day,
    next_working_day,
    previous_working_day,
    working_days_between,
)
from app.calendar.nepali_calendar import due_label
from app.services.report_service import _days_overdue


class WeekendTests(unittest.TestCase):
    """Saturday AND Sunday are the weekend for every tenant."""

    def test_weekend_constant_is_saturday_and_sunday(self):
        self.assertEqual(NEPAL_WEEKEND, frozenset({5, 6}))

    def test_saturday_is_weekend_and_not_a_working_day(self):
        info = classify_day(date(2025, 10, 11), holiday_map={})
        self.assertTrue(info.is_weekend)
        self.assertFalse(info.is_working_day)

    def test_sunday_is_weekend_and_not_a_working_day(self):
        info = classify_day(date(2025, 10, 12), holiday_map={})
        self.assertTrue(info.is_weekend)
        self.assertFalse(info.is_working_day)

    def test_monday_to_friday_are_working_days(self):
        for day in range(6, 11):  # Mon 2025-10-06 .. Fri 2025-10-10
            info = classify_day(date(2025, 10, day), holiday_map={})
            self.assertFalse(info.is_weekend, day)
            self.assertTrue(info.is_working_day, day)

    def test_saturday_only_still_available_via_override(self):
        info = classify_day(date(2025, 10, 12), weekend={5}, holiday_map={})
        self.assertFalse(info.is_weekend)


class WorkingDayNavigationTests(unittest.TestCase):
    """2026-10-16 is a Friday, so 17 = Saturday, 18 = Sunday, 19 = Monday."""

    FRI, SAT, SUN, MON = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)

    def test_next_working_day(self):
        self.assertEqual(next_working_day(self.FRI), self.FRI)
        self.assertEqual(next_working_day(self.SAT), self.MON)
        self.assertEqual(next_working_day(self.SUN), self.MON)

    def test_previous_working_day(self):
        self.assertEqual(previous_working_day(self.MON), self.MON)
        self.assertEqual(previous_working_day(self.SAT), self.FRI)
        self.assertEqual(previous_working_day(self.SUN), self.FRI)

    def test_weekend_override_is_honoured(self):
        # Saturday-only weekend: Sunday is a working day again.
        self.assertEqual(next_working_day(self.SUN, weekend={5}), self.SUN)
        self.assertEqual(next_working_day(self.SAT, weekend={5}), self.SUN)

    def test_a_week_of_only_weekend_days_is_rejected_not_looped_forever(self):
        with self.assertRaises(ValueError):
            next_working_day(self.SAT, weekend=range(7))
        with self.assertRaises(ValueError):
            previous_working_day(self.SAT, weekend=range(7))


class WorkingDaysBetweenTests(unittest.TestCase):
    """Working days in (start, end] — the "days overdue" count."""

    FRI, SAT, SUN, MON, TUE = (date(2026, 10, d) for d in (16, 17, 18, 19, 20))

    def test_same_day_and_reversed_ranges_are_zero(self):
        self.assertEqual(working_days_between(self.FRI, self.FRI), 0)
        self.assertEqual(working_days_between(self.MON, self.FRI), 0)

    def test_friday_to_the_weekend_is_still_zero(self):
        self.assertEqual(working_days_between(self.FRI, self.SAT), 0)
        self.assertEqual(working_days_between(self.FRI, self.SUN), 0)

    def test_friday_to_monday_is_one(self):
        self.assertEqual(working_days_between(self.FRI, self.MON), 1)
        self.assertEqual(working_days_between(self.FRI, self.TUE), 2)

    def test_starting_on_a_weekend_day(self):
        self.assertEqual(working_days_between(self.SAT, self.MON), 1)
        self.assertEqual(working_days_between(self.SUN, self.MON), 1)

    def test_whole_weeks_are_five_working_days_each(self):
        self.assertEqual(working_days_between(self.FRI, date(2026, 10, 23)), 5)
        self.assertEqual(working_days_between(self.FRI, date(2026, 10, 30)), 10)
        self.assertEqual(working_days_between(self.MON, date(2026, 11, 16)), 20)

    def test_matches_a_day_by_day_count_for_every_start_and_length(self):
        for offset in range(14):
            start = self.FRI + timedelta(days=offset)
            for length in range(0, 60):
                end = start + timedelta(days=length)
                expected = sum(
                    1 for i in range(1, length + 1)
                    if (start + timedelta(days=i)).weekday() not in NEPAL_WEEKEND
                )
                self.assertEqual(working_days_between(start, end), expected, (start, end))

    def test_saturday_only_weekend_override(self):
        # Fri -> Mon with only Saturday off: Sun and Mon count.
        self.assertEqual(working_days_between(self.FRI, self.MON, weekend={5}), 2)


class OverdueLabelTests(unittest.TestCase):
    """"Nd overdue" counts working days: a task due Friday is not 1d overdue
    on Saturday."""

    FRI, SAT, SUN, MON, TUE = (date(2026, 10, d) for d in (16, 17, 18, 19, 20))

    def test_over_the_weekend_it_just_says_overdue(self):
        self.assertEqual(due_label(self.FRI, today=self.SAT), "Overdue")
        self.assertEqual(due_label(self.FRI, today=self.SUN), "Overdue")

    def test_counts_resume_on_monday(self):
        self.assertEqual(due_label(self.FRI, today=self.MON), "1d overdue")
        self.assertEqual(due_label(self.FRI, today=self.TUE), "2d overdue")

    def test_a_thursday_task_on_sunday_is_one_working_day_overdue(self):
        self.assertEqual(due_label(date(2026, 10, 15), today=self.SUN), "1d overdue")

    def test_not_yet_overdue_labels_are_unchanged(self):
        self.assertEqual(due_label(self.MON, today=self.MON), "Due today")
        self.assertEqual(due_label(self.SAT, today=self.FRI), "Due tomorrow")
        self.assertIsNone(due_label(None, today=self.MON))


class OverdueReportDaysTests(unittest.TestCase):
    def test_days_overdue_skips_weekends(self):
        fri, sat, mon = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 19)
        self.assertEqual(_days_overdue(fri, sat), 0)
        self.assertEqual(_days_overdue(fri, mon), 1)
        self.assertEqual(_days_overdue(date(2026, 10, 9), mon), 6)  # Fri a week earlier

    def test_no_due_date_or_not_yet_due_is_zero(self):
        self.assertEqual(_days_overdue(None, date(2026, 10, 19)), 0)
        self.assertEqual(_days_overdue(date(2026, 10, 20), date(2026, 10, 19)), 0)


if __name__ == "__main__":
    unittest.main()
