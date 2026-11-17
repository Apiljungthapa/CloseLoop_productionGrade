import unittest
from datetime import date

from app.calendar import is_non_working_day, set_configured_holidays
from app.services import timesheet_reminders as tr
from app.services.timesheet_reminders import (
    AD, BS, DUE, OVERDUE, UPCOMING_1, UPCOMING_3, ad_month, banner_status, bs_month,
    obligations, reminder_message, stage,
)


class ObligationTests(unittest.TestCase):
    def setUp(self):
        set_configured_holidays({})   # weekend-only unless a test adds holidays

    def test_ad_month_ends(self):
        self.assertEqual(ad_month(2026, 10).deadline, date(2026, 10, 31))
        self.assertEqual(ad_month(2026, 2).deadline, date(2026, 2, 28))
        self.assertEqual(ad_month(2028, 2).deadline, date(2028, 2, 29))   # leap year
        self.assertEqual(ad_month(2026, 12).end, date(2026, 12, 31))

    def test_bs_month_ends_use_the_real_month_lengths(self):
        ashwin, kartik, mangsir = bs_month(2083, 6), bs_month(2083, 7), bs_month(2083, 8)
        self.assertEqual((ashwin.start, ashwin.deadline), (date(2026, 9, 17), date(2026, 10, 17)))
        self.assertEqual(kartik.deadline, date(2026, 11, 16))   # 30 days
        self.assertEqual(mangsir.deadline, date(2026, 12, 15))  # 29 days
        self.assertEqual(kartik.label, "Kartik 2083 BS")

    def test_bs_deadline_differs_from_ad_deadline(self):
        self.assertNotEqual(bs_month(2083, 7).deadline, ad_month(2026, 10).deadline)

    def test_lists_current_and_previous_months_of_both_calendars(self):
        obs = obligations(date(2026, 10, 28), lookback=2)
        ad = [o.period_key for o in obs if o.calendar == AD]
        bs = [o.period_key for o in obs if o.calendar == BS]
        self.assertEqual(ad, ["2026-10", "2026-09", "2026-08"])
        self.assertEqual(bs, ["2083-07", "2083-06", "2083-05"])

    def test_year_boundary_in_both_calendars(self):
        obs = obligations(date(2027, 1, 5), lookback=1)
        self.assertEqual([o.period_key for o in obs if o.calendar == AD], ["2027-01", "2026-12"])
        bs_keys = [o.period_key for o in obs if o.calendar == BS]
        self.assertEqual(bs_keys, ["2083-09", "2083-08"])


class StageTests(unittest.TestCase):
    def setUp(self):
        set_configured_holidays({})

    def test_working_day_deadline_gets_3_1_0_day_reminders(self):
        ob = ad_month(2026, 9)   # ends Wed 2026-09-30
        self.assertEqual(date(2026, 9, 30).weekday(), 2)
        self.assertEqual(stage(ob, date(2026, 9, 27)), None)   # Sunday: day off
        self.assertEqual(stage(ob, date(2026, 9, 28)), None)   # Mon: D-2, nothing
        self.assertEqual(stage(ob, date(2026, 9, 29)), UPCOMING_1)
        self.assertEqual(stage(ob, date(2026, 9, 30)), DUE)
        self.assertEqual(stage(ob, date(2026, 9, 25)), UPCOMING_3)   # D-3 is Sunday -> Friday
        self.assertEqual(stage(ob, date(2026, 9, 24)), None)

    def test_three_days_before(self):
        ob = bs_month(2083, 7)   # ends Mon 2026-11-16
        self.assertEqual(date(2026, 11, 16).weekday(), 0)
        self.assertFalse(is_non_working_day(date(2026, 11, 13)))
        # Fri 13th is D-3, and also where D-1 (Sunday) shifts to: one reminder, the stronger
        self.assertEqual(stage(ob, date(2026, 11, 13)), UPCOMING_1)
        self.assertEqual(stage(ob, date(2026, 11, 12)), None)
        self.assertEqual(stage(ob, date(2026, 11, 15)), None)       # D-1 is Sunday: day off
        self.assertEqual(stage(ob, date(2026, 11, 16)), DUE)

    def test_deadline_on_a_day_off_is_reminded_on_the_working_day_before(self):
        ob = ad_month(2026, 10)   # ends Sat 2026-10-31
        self.assertEqual(date(2026, 10, 31).weekday(), 5)
        self.assertEqual(stage(ob, date(2026, 10, 30)), DUE)     # Friday carries "due"
        self.assertIsNone(stage(ob, date(2026, 10, 31)))         # Saturday: no reminders
        self.assertIsNone(stage(ob, date(2026, 11, 1)))          # Sunday: none

    def test_overdue_every_working_day_after_the_deadline(self):
        ob = ad_month(2026, 10)
        self.assertEqual(stage(ob, date(2026, 11, 2)), OVERDUE)   # Mon
        self.assertEqual(stage(ob, date(2026, 11, 3)), OVERDUE)
        self.assertIsNone(stage(ob, date(2026, 11, 7)))           # Sat
        self.assertEqual(stage(ob, date(2027, 1, 5)), OVERDUE)    # still overdue later

    def test_recorded_holidays_are_not_reminder_days(self):
        set_configured_holidays({date(2026, 11, 3): "Test holiday"})
        ob = ad_month(2026, 10)
        self.assertEqual(stage(ob, date(2026, 11, 2)), OVERDUE)
        self.assertIsNone(stage(ob, date(2026, 11, 3)))
        self.assertEqual(stage(ob, date(2026, 11, 4)), OVERDUE)

    def test_deadline_after_a_holiday_shifts_to_the_previous_working_day(self):
        set_configured_holidays({date(2026, 9, 30): "Test holiday"})
        ob = ad_month(2026, 9)
        self.assertIsNone(stage(ob, date(2026, 9, 30)))
        self.assertEqual(stage(ob, date(2026, 9, 29)), DUE)


class BannerTests(unittest.TestCase):
    def test_banner_statuses(self):
        ob = ad_month(2026, 10)
        self.assertIsNone(banner_status(ob, date(2026, 10, 20)))
        self.assertEqual(banner_status(ob, date(2026, 10, 28)), "UPCOMING")
        self.assertEqual(banner_status(ob, date(2026, 10, 31)), DUE)
        self.assertEqual(banner_status(ob, date(2026, 11, 1)), OVERDUE)   # shows on days off too


class MessageTests(unittest.TestCase):
    def test_messages_name_the_calendar_deadline_and_the_tasks(self):
        ob = bs_month(2083, 7)
        late = reminder_message(ob, OVERDUE, 3, 150, date(2026, 11, 19))
        self.assertIn("overdue by 3 days", late)
        self.assertIn("3 tasks you submitted (2h 30m)", late)
        self.assertIn("due today (2026-11-16)", reminder_message(ob, DUE, 1, 45, date(2026, 11, 16)))
        soon = reminder_message(ob, UPCOMING_1, 1, 45, date(2026, 11, 15))
        self.assertNotIn("(BS)", soon)                      # the label already says BS
        self.assertIn("(AD)", reminder_message(ad_month(2026, 10), DUE, 1, 45, date(2026, 10, 30)))
        self.assertIn("due in 1 day ", soon)
        self.assertIn("1 task you submitted (45m) in Kartik 2083 BS is not yet sent", soon)

    def test_a_task_with_no_time_taken_is_counted_without_inventing_hours(self):
        msg = reminder_message(bs_month(2083, 7), DUE, 2, 0, date(2026, 11, 16))
        self.assertIn("2 tasks you submitted in", msg)
        self.assertNotIn("0m", msg)


if __name__ == "__main__":
    unittest.main()
