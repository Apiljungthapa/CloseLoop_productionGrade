import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from app.calendar import (
    NEPAL_WEEKEND,
    bs_iso,
    classify_day,
    configured_holidays,
    configured_holidays_in,
    is_non_working_day,
    known_holidays,
    next_working_day,
    previous_working_day,
    set_configured_holidays,
    working_days_between,
)
from app.calendar.nepali_calendar import due_label
from app.models import DueAnchor, DueDateType, Holiday, RecurrenceFrequency as F
from app.services import holiday_service
from app.services.due_date_rules import (
    DueDateOutOfRangeError,
    NonWorkingDayError,
    apply_due_days,
    resolve_generated_due_date,
    roll_to_working_day,
    validate_working_day,
)
from app.services.plan_generation_service import preview_tasks_for_period
from app.services.recurrence import occurrence_dates

# 2026-10-16 is a Friday: 17 = Sat, 18 = Sun, 19 = Mon, 20 = Tue, 21 = Wed,
# 22 = Thu, 23 = Fri.
FRI, SAT, SUN, MON, TUE, WED, THU, FRI2 = (date(2026, 10, d) for d in (16, 17, 18, 19, 20, 21, 22, 23))


class HolidayTestCase(unittest.TestCase):
    """Starts every test with no configured holidays and puts that back."""

    def setUp(self):
        set_configured_holidays({})
        self.addCleanup(set_configured_holidays, {})


class RegistryTests(HolidayTestCase):
    def test_it_starts_empty_so_nothing_changes_until_a_holiday_is_configured(self):
        self.assertEqual(configured_holidays(), {})
        self.assertFalse(is_non_working_day(MON))
        self.assertTrue(is_non_working_day(SAT))

    def test_set_replaces_everything_it_does_not_merge(self):
        set_configured_holidays({MON: "A"})
        set_configured_holidays({TUE: "B"})
        self.assertEqual(configured_holidays(), {TUE: "B"})

    def test_the_returned_copy_cannot_corrupt_the_registry(self):
        set_configured_holidays({MON: "A"})
        configured_holidays()[TUE] = "sneaky"
        self.assertEqual(configured_holidays(), {MON: "A"})

    def test_range_lookup_is_inclusive_and_sorted(self):
        set_configured_holidays({THU: "c", MON: "a", WED: "b"})
        self.assertEqual(list(configured_holidays_in(MON, WED)), [MON, WED])
        self.assertEqual(configured_holidays_in(TUE, TUE), {})


class NonWorkingDayTests(HolidayTestCase):
    def test_a_configured_holiday_is_a_non_working_day(self):
        set_configured_holidays({WED: "Dashain"})
        self.assertTrue(is_non_working_day(WED))
        self.assertFalse(is_non_working_day(THU))

    def test_weekend_override_still_applies_alongside_holidays(self):
        set_configured_holidays({WED: "Dashain"})
        self.assertFalse(is_non_working_day(SUN, weekend={5}))
        self.assertTrue(is_non_working_day(WED, weekend={5}))

    def test_next_working_day_skips_a_weekend_and_a_holiday_run(self):
        set_configured_holidays({MON: "Dashain 1", TUE: "Dashain 2", WED: "Dashain 3"})
        self.assertEqual(next_working_day(SAT), THU)
        self.assertEqual(next_working_day(MON), THU)
        self.assertEqual(next_working_day(FRI), FRI)

    def test_previous_working_day_skips_a_weekend_and_a_holiday_run(self):
        wed_before, thu_before = date(2026, 10, 14), date(2026, 10, 15)
        set_configured_holidays({FRI: "Holiday 1", thu_before: "Holiday 2"})
        self.assertEqual(previous_working_day(SUN), wed_before)
        self.assertEqual(previous_working_day(FRI), wed_before)
        self.assertEqual(previous_working_day(MON), MON)

    def test_a_holiday_in_the_middle_of_the_week(self):
        set_configured_holidays({WED: "Holiday"})
        self.assertEqual(next_working_day(WED), THU)
        self.assertEqual(previous_working_day(WED), TUE)

    def test_the_every_day_is_a_weekend_guard_still_holds(self):
        with self.assertRaises(ValueError):
            next_working_day(WED, weekend=range(7))


class WorkingDaysBetweenTests(HolidayTestCase):
    def test_a_holiday_inside_the_range_comes_off_the_count(self):
        set_configured_holidays({MON: "Holiday"})
        # Fri -> Tue: Mon is a holiday, only Tue counts.
        self.assertEqual(working_days_between(FRI, TUE), 1)
        self.assertEqual(working_days_between(FRI, MON), 0)

    def test_a_holiday_on_the_start_day_is_not_counted_again(self):
        set_configured_holidays({FRI: "Holiday"})
        self.assertEqual(working_days_between(FRI, MON), 1)

    def test_a_holiday_on_a_weekend_is_not_a_second_lost_day(self):
        set_configured_holidays({SAT: "Holiday on a Saturday"})
        self.assertEqual(working_days_between(FRI, MON), 1)

    def test_matches_a_day_by_day_count_with_several_holidays(self):
        holidays = {
            date(2026, 10, 19): "a", date(2026, 10, 24): "on a Saturday",
            date(2026, 10, 28): "b", date(2026, 10, 29): "c", date(2026, 11, 9): "d",
        }
        set_configured_holidays(holidays)
        for offset in range(0, 21):
            start = FRI + timedelta(days=offset)
            for length in range(0, 50):
                end = start + timedelta(days=length)
                expected = sum(
                    1 for i in range(1, length + 1)
                    if (start + timedelta(days=i)).weekday() not in NEPAL_WEEKEND
                    and (start + timedelta(days=i)) not in holidays
                )
                self.assertEqual(working_days_between(start, end), expected, (start, end))

    def test_due_label_skips_the_holiday(self):
        set_configured_holidays({MON: "Holiday"})
        self.assertEqual(due_label(FRI, today=MON), "Overdue")
        self.assertEqual(due_label(FRI, today=TUE), "1d overdue")
        self.assertEqual(due_label(FRI, today=WED), "2d overdue")


class ClassifyDayTests(HolidayTestCase):
    def test_a_configured_holiday_is_not_a_working_day(self):
        set_configured_holidays({WED: "Dashain"})
        info = classify_day(WED, holiday_map={})
        self.assertEqual(info.holiday, "Dashain")
        self.assertFalse(info.is_working_day)

    def test_the_configured_name_wins_over_the_librarys_estimate(self):
        set_configured_holidays({WED: "Confirmed name"})
        info = classify_day(WED, holiday_map={WED: "Estimated name"})
        self.assertEqual(info.holiday, "Confirmed name")

    def test_known_holidays_merges_estimates_with_configured_ones(self):
        set_configured_holidays({date(2026, 10, 21): "Mine", date(2027, 3, 1): "Next year"})
        merged = known_holidays([2026])
        self.assertEqual(merged[date(2026, 10, 21)], "Mine")
        self.assertNotIn(date(2027, 3, 1), merged)  # only the years asked for


class RollToWorkingDayTests(HolidayTestCase):
    def test_a_holiday_rolls_to_the_next_working_day(self):
        set_configured_holidays({MON: "Holiday"})
        self.assertEqual(roll_to_working_day(MON), TUE)

    def test_a_weekend_that_runs_into_a_holiday_rolls_past_both(self):
        set_configured_holidays({MON: "Holiday"})
        self.assertEqual(roll_to_working_day(SAT), TUE)
        self.assertEqual(roll_to_working_day(SUN), TUE)

    def test_prefer_earlier_goes_back_past_the_holiday(self):
        set_configured_holidays({FRI: "Holiday"})
        self.assertEqual(roll_to_working_day(SAT, prefer_later=False), date(2026, 10, 15))

    def test_the_upper_bound_forces_the_earlier_day(self):
        set_configured_holidays({THU: "Holiday"})
        self.assertEqual(roll_to_working_day(THU, hi=THU), WED)

    def test_the_lower_bound_forces_the_later_day(self):
        set_configured_holidays({THU: "Holiday"})
        self.assertEqual(roll_to_working_day(THU, lo=THU, prefer_later=False), FRI2)

    def test_a_window_of_only_non_working_days_returns_the_date_as_is(self):
        set_configured_holidays({MON: "Holiday"})
        self.assertEqual(roll_to_working_day(MON, lo=SAT, hi=MON), MON)

    def test_a_working_day_is_untouched(self):
        set_configured_holidays({MON: "Holiday"})
        self.assertEqual(roll_to_working_day(TUE), TUE)


class ApplyDueDaysTests(HolidayTestCase):
    def test_an_occurrence_due_on_a_holiday_is_due_the_next_working_day(self):
        set_configured_holidays({WED: "Holiday"})
        self.assertEqual(apply_due_days(WED, 0), THU)
        self.assertEqual(apply_due_days(TUE, 1), THU)

    def test_friday_plus_a_day_skips_the_weekend_and_a_monday_holiday(self):
        set_configured_holidays({MON: "Holiday"})
        self.assertEqual(apply_due_days(FRI, 1), TUE)

    def test_without_holidays_it_is_unchanged(self):
        self.assertEqual(apply_due_days(WED, 0), WED)
        self.assertEqual(apply_due_days(FRI, 1), MON)


class ResolveGeneratedDueDateTests(HolidayTestCase):
    START, END = date(2026, 10, 1), date(2026, 10, 31)  # Oct 1 2026 is a Thursday
    WITHIN, AFTER = DueDateType.WITHIN_PERIOD, DueDateType.AFTER_PERIOD_END

    def _resolve(self, n, anchor, due_type=None, post_days=0, **kw):
        return resolve_generated_due_date(
            self.START, self.END, n, anchor, due_type or self.WITHIN, post_days, **kw
        )

    def test_period_start_anchor_rolls_forward_off_a_holiday(self):
        set_configured_holidays({date(2026, 10, 8): "Holiday"})  # day 8 = Thursday
        self.assertEqual(self._resolve(8, DueAnchor.PERIOD_START), date(2026, 10, 9))

    def test_period_end_anchor_rolls_backward_off_a_holiday(self):
        # "3 days from close" = day 29 from the start = Oct 29 (Thu).
        set_configured_holidays({date(2026, 10, 29): "Holiday"})
        self.assertEqual(self._resolve(3, DueAnchor.PERIOD_END), date(2026, 10, 28))

    def test_after_period_end_is_the_last_working_day_before_a_holiday(self):
        # Period ends Sat Oct 31 -> Fri Oct 30; with Oct 30 a holiday -> Thu Oct 29.
        # (The old "+5 days" date, Nov 5, belongs to the next period.)
        self.assertEqual(
            self._resolve(None, DueAnchor.PERIOD_START, self.AFTER, 5), date(2026, 10, 30)
        )
        set_configured_holidays({date(2026, 10, 30): "Holiday"})
        self.assertEqual(
            self._resolve(None, DueAnchor.PERIOD_START, self.AFTER, 5), date(2026, 10, 29)
        )

    def test_a_holiday_at_the_end_of_the_period_stays_inside_the_period(self):
        # Oct 30 (Fri) is the day-30 date; Oct 31 is a Saturday.
        set_configured_holidays({date(2026, 10, 30): "Holiday"})
        due = self._resolve(30, DueAnchor.PERIOD_START)
        self.assertEqual(due, date(2026, 10, 29))  # next working day would pass period end
        self.assertLessEqual(due, self.END)

    def test_roll_non_working_false_gives_the_raw_date(self):
        set_configured_holidays({date(2026, 10, 8): "Holiday"})
        self.assertEqual(
            self._resolve(8, DueAnchor.PERIOD_START, roll_non_working=False), date(2026, 10, 8)
        )


class ValidateWorkingDayTests(HolidayTestCase):
    def test_a_holiday_is_rejected_with_its_name_and_date(self):
        set_configured_holidays({WED: "Dashain"})
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(WED, label="Sub-task")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("Sub-task due date 2026-10-21"))
        self.assertIn("holiday", msg)
        self.assertIn("Dashain", msg)

    def test_a_weekend_keeps_its_original_message(self):
        set_configured_holidays({WED: "Dashain"})
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(SAT)
        self.assertIn("Saturday", str(ctx.exception))
        self.assertIn("weekends", str(ctx.exception))

    def test_a_holiday_on_a_weekend_reports_the_weekend(self):
        set_configured_holidays({SAT: "Weekend holiday"})
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(SAT)
        self.assertIn("Saturday", str(ctx.exception))

    def test_resubmitting_the_stored_date_is_allowed_after_a_holiday_is_added(self):
        # The holiday was added AFTER this task's date was picked.
        set_configured_holidays({WED: "Dashain"})
        validate_working_day(WED, current=WED)

    def test_changing_to_a_different_holiday_is_rejected(self):
        set_configured_holidays({WED: "A", THU: "B"})
        with self.assertRaises(NonWorkingDayError):
            validate_working_day(THU, current=WED)

    def test_a_working_day_and_no_date_are_fine(self):
        set_configured_holidays({WED: "Dashain"})
        validate_working_day(THU)
        validate_working_day(None)

    def test_is_caught_by_the_existing_out_of_range_handlers(self):
        self.assertTrue(issubclass(NonWorkingDayError, DueDateOutOfRangeError))


class RecurrenceTests(HolidayTestCase):
    def _occ(self, frequency, start, end, **kw):
        base = dict(interval_count=1, days_of_week=None, end=None)
        base.update(kw)
        return occurrence_dates(
            frequency=frequency, start=start, range_start=start, range_end=end, **base
        )

    def test_daily_skips_a_holiday_like_a_weekend(self):
        set_configured_holidays({WED: "Holiday"})
        days = self._occ(F.DAILY, date(2026, 10, 19), date(2026, 10, 23))
        self.assertEqual(days, [MON, TUE, THU, FRI2])

    def test_daily_without_holidays_is_unchanged(self):
        self.assertEqual(
            self._occ(F.DAILY, date(2026, 10, 19), date(2026, 10, 23)),
            [MON, TUE, WED, THU, FRI2],
        )

    def test_weekly_keeps_an_occurrence_that_lands_on_a_holiday(self):
        # Skipping it would lose the week's task; its DUE date rolls instead.
        set_configured_holidays({WED: "Holiday"})
        days = self._occ(F.WEEKLY, date(2026, 10, 19), date(2026, 10, 25), days_of_week=[2])
        self.assertEqual(days, [WED])
        self.assertEqual(apply_due_days(WED, 0), THU)

    def test_weekly_still_drops_a_legacy_weekend_day(self):
        days = self._occ(F.WEEKLY, date(2026, 10, 12), date(2026, 10, 25), days_of_week=[2, 5])
        self.assertEqual(days, [date(2026, 10, 14), WED])

    def test_monthly_keeps_the_raw_occurrence_on_a_holiday(self):
        set_configured_holidays({WED: "Holiday"})
        days = self._occ(F.MONTHLY, WED, date(2026, 12, 31))
        self.assertEqual(days[0], WED)


class DailyIntervalMovesNotDropsTests(HolidayTestCase):
    """A Daily task "every N days" keeps its cadence: an occurrence on a
    weekend/holiday happens on the next working day instead of vanishing.
    (Dropping it made "every 4 days" show 1 -> 9 -> 17 whenever the 4-day
    step landed on Saturday / Sunday.)"""

    def _daily(self, start, range_start, range_end, interval, end=None):
        return occurrence_dates(
            frequency=F.DAILY, interval_count=interval, days_of_week=None, start=start,
            end=end, range_start=range_start, range_end=range_end,
        )

    # Mangsir 1 2083 = Tuesday 2026-11-17, the reported case.
    START = date(2026, 11, 17)

    def test_every_4_days_from_a_tuesday_loses_nothing(self):
        got = self._daily(self.START, self.START, date(2026, 12, 15), 4)
        self.assertEqual(got, [
            date(2026, 11, 17),  # Tue  Mangsir 1
            date(2026, 11, 23),  # Sat 21 -> Mon  Mangsir 7
            date(2026, 11, 25),  # Wed  Mangsir 9
            date(2026, 11, 30),  # Sun 29 -> Mon  Mangsir 14
            date(2026, 12, 3),   # Thu  Mangsir 17
            date(2026, 12, 7),   # Mon  Mangsir 21
            date(2026, 12, 11),  # Fri  Mangsir 25
            date(2026, 12, 15),  # Tue  Mangsir 29
        ])

    def test_one_task_per_cadence_step_and_never_a_long_hole(self):
        end = self.START + timedelta(days=400)
        for interval in (2, 3, 4, 5, 6, 7, 9, 10):
            got = self._daily(self.START, self.START, end, interval)
            # Every step whose moved date still fits the range is there (two
            # steps can only share a day when the interval is 1 or 2).
            steps = [self.START + timedelta(days=i * interval)
                     for i in range((end - self.START).days // interval + 1)]
            fitting = [s for s in steps if next_working_day(s) <= end]
            if interval >= 3:
                self.assertEqual(len(got), len(fitting), interval)
            # ...and the longest gap is one step plus the weekend it was pushed over.
            gaps = [(b - a).days for a, b in zip(got, got[1:])]
            self.assertLessEqual(max(gaps), interval + 2, (interval, max(gaps)))
            self.assertTrue(all(not is_non_working_day(d) for d in got), interval)
            self.assertEqual(got, sorted(set(got)), interval)

    def test_every_day_is_still_every_working_day(self):
        got = self._daily(FRI, FRI, date(2026, 10, 27), 1)
        # Sat, Sun and Mon all settle on Mon: one task, not three.
        self.assertEqual(got, [FRI, MON, TUE, WED, THU, FRI2, date(2026, 10, 26), date(2026, 10, 27)])

    def test_two_that_settle_on_one_day_are_one_task(self):
        # Every 2 days from Thu: Thu, Sat, Mon, Wed — Sat moves onto Mon, which is
        # already an occurrence.
        got = self._daily(THU, THU, date(2026, 10, 28), 2)
        self.assertEqual(got, [THU, date(2026, 10, 26), date(2026, 10, 28)])

    def test_a_weekend_step_just_before_the_range_lands_inside_it(self):
        # Saturday 2026-11-21 is a step of the cadence; it happens Monday 23rd.
        got = self._daily(self.START, date(2026, 11, 23), date(2026, 11, 30), 4)
        self.assertEqual(got, [date(2026, 11, 23), date(2026, 11, 25), date(2026, 11, 30)])
        # ...and the step is not repeated in the range that holds the Saturday.
        got = self._daily(self.START, date(2026, 11, 18), date(2026, 11, 22), 4)
        self.assertEqual(got, [])

    def test_a_holiday_run_pushes_the_step_to_the_first_working_day_after_it(self):
        # Sat 21 step, and Mon 23 + Tue 24 are holidays -> Wed 25 (which is also
        # the next step, so they are one task).
        set_configured_holidays({date(2026, 11, 23): "H1", date(2026, 11, 24): "H2"})
        got = self._daily(self.START, self.START, date(2026, 11, 30), 4)
        self.assertEqual(got, [date(2026, 11, 17), date(2026, 11, 25), date(2026, 11, 30)])

    def test_windows_agree_with_one_big_range(self):
        # Generation asks one period at a time; the pieces must add up to the
        # whole with nothing doubled or lost on a boundary.
        set_configured_holidays({date(2026, 11, 26): "H", date(2026, 12, 8): "H"})
        last = date(2027, 2, 28)
        for interval in range(1, 11):
            whole = self._daily(self.START, self.START, last, interval)
            pieces, lo = [], self.START
            for hi in (date(2026, 11, 30), date(2026, 12, 7), date(2026, 12, 8),
                       date(2026, 12, 31), date(2027, 1, 15), last):
                pieces += self._daily(self.START, lo, hi, interval)
                lo = hi + timedelta(days=1)
            self.assertEqual(pieces, whole, interval)

    def test_a_step_is_never_moved_past_the_series_end(self):
        # Series ends Sunday 22nd: the Saturday step would have to move to Monday
        # 23rd, after the end, so it is not generated.
        got = self._daily(self.START, self.START, date(2026, 12, 31), 4, end=date(2026, 11, 22))
        self.assertEqual(got, [date(2026, 11, 17)])
        # An end on a working day keeps a step that settles on it.
        got = self._daily(self.START, self.START, date(2026, 12, 31), 4, end=date(2026, 11, 23))
        self.assertEqual(got, [date(2026, 11, 17), date(2026, 11, 23)])

    def test_the_first_step_on_a_weekend_is_not_lost(self):
        got = self._daily(SAT, SAT, date(2026, 10, 31), 4)
        self.assertEqual(got[0], MON)

    def test_preview_shows_the_moved_dates_as_the_task_dates(self):
        rows = preview_tasks_for_period(
            [_tmpl(frequency="DAILY", interval_count=4, recurrence_start=self.START)],
            _period(date(2026, 11, 17), date(2026, 12, 15)),
        )
        days = sorted(r["occurrence_date"] for r in rows)
        self.assertEqual(days[:4], [date(2026, 11, 17), date(2026, 11, 23),
                                    date(2026, 11, 25), date(2026, 11, 30)])
        self.assertEqual(len(days), 8)
        self.assertTrue(all(r["due_date"] == r["occurrence_date"] for r in rows))


def _tmpl(**kw):
    base = dict(title="T", interval_count=1, days_of_week=None, due_days=0, sub_task_templates=[])
    base.update(kw)
    return base


def _period(start, end):
    return SimpleNamespace(start_date=start, end_date=end)


class GeneratedTasksTests(HolidayTestCase):
    """The preview is the same date math real generation uses."""

    def test_monthly_occurrence_on_a_holiday_is_due_the_next_working_day(self):
        set_configured_holidays({WED: "Holiday"})
        rows = preview_tasks_for_period(
            [_tmpl(frequency="MONTHLY", recurrence_start=WED)],
            _period(date(2026, 10, 1), date(2026, 10, 31)),
        )
        self.assertEqual(rows[0]["occurrence_date"], WED)  # identity stays raw
        self.assertEqual(rows[0]["due_date"], THU)

    def test_once_per_period_day_n_on_a_holiday_rolls(self):
        # Oct 1 2026 is a Thursday; day 8 = Thursday Oct 8.
        set_configured_holidays({date(2026, 10, 8): "Holiday"})
        rows = preview_tasks_for_period(
            [_tmpl(due_day_of_period=8, due_anchor="PERIOD_START")],
            _period(date(2026, 10, 1), date(2026, 10, 31)),
        )
        self.assertEqual(rows[0]["due_date"], date(2026, 10, 9))

    def test_daily_has_no_task_on_a_holiday(self):
        set_configured_holidays({WED: "Holiday"})
        rows = preview_tasks_for_period(
            [_tmpl(frequency="DAILY", recurrence_start=MON)],
            _period(MON, FRI2),
        )
        self.assertEqual(
            sorted(r["occurrence_date"] for r in rows), [MON, TUE, THU, FRI2]
        )


class UpsertHolidaysTests(unittest.IsolatedAsyncioTestCase):
    """upsert_holidays against a stand-in session: it must create, update,
    leave alone, and never let an API batch overwrite a MANUAL row. A holiday
    is identified by its date AND name, so several can share a date."""

    def _db(self, existing=()):
        added = []
        result = mock.Mock()
        result.scalars.return_value.all.return_value = list(existing)
        db = SimpleNamespace(
            add=added.append,
            flush=mock.AsyncMock(),
            execute=mock.AsyncMock(return_value=result),
        )
        return db, added

    def _row(self, day, name, source, category="ALL"):
        return Holiday(
            holiday_date=day, bs_date=bs_iso(day), name=name, source=source, category=category
        )

    async def test_new_dates_are_created(self):
        db, added = self._db()
        counts = await holiday_service.upsert_holidays(db, [(MON, "A"), (TUE, "B")])
        self.assertEqual(counts, (2, 0, 0))
        self.assertEqual({(h.holiday_date, h.name, h.source) for h in added},
                         {(MON, "A", "MANUAL"), (TUE, "B", "MANUAL")})

    async def test_a_different_name_on_the_same_date_is_a_second_holiday(self):
        row = self._row(MON, "Old", "MANUAL")
        db, added = self._db([row])
        counts = await holiday_service.upsert_holidays(db, [(MON, "New")])
        self.assertEqual(counts, (1, 0, 0))
        self.assertEqual((row.name, [h.name for h in added]), ("Old", ["New"]))

    async def test_the_same_holiday_in_the_same_batch_is_stored_once(self):
        db, added = self._db()
        await holiday_service.upsert_holidays(db, [(MON, "first"), (MON, "First "), (MON, "second")])
        self.assertEqual(sorted(h.name for h in added), ["First", "second"])

    async def test_an_identical_entry_is_a_no_op(self):
        row = self._row(MON, "Same", "MANUAL")
        db, _ = self._db([row])
        self.assertEqual(await holiday_service.upsert_holidays(db, [(MON, "Same")]), (0, 0, 0))

    async def test_changed_remarks_or_category_update_the_row(self):
        row = self._row(MON, "Teej", "MANUAL")
        db, _ = self._db([row])
        draft = holiday_service.HolidayDraft(MON, "Teej", "GENDER", "FEMALE", "Only for female staff")
        self.assertEqual(await holiday_service.upsert_holidays(db, [draft]), (0, 1, 0))
        self.assertEqual((row.category, row.audience, row.remarks),
                         ("GENDER", "FEMALE", "Only for female staff"))

    async def test_an_api_batch_never_overwrites_a_manual_row(self):
        row = self._row(MON, "Corrected by an admin", "MANUAL")
        db, added = self._db([row])
        counts = await holiday_service.upsert_holidays(db, [(MON, "Feed name")], source="API")
        self.assertEqual(counts, (0, 0, 1))
        self.assertEqual((row.name, row.source, added), ("Corrected by an admin", "MANUAL", []))

    async def test_an_api_row_with_a_manual_name_match_is_skipped_too(self):
        row = self._row(MON, "Dashain", "MANUAL")
        db, _ = self._db([row])
        draft = holiday_service.HolidayDraft(MON, "Dashain", remarks="from the feed")
        self.assertEqual(await holiday_service.upsert_holidays(db, [draft], source="API"), (0, 0, 1))
        self.assertIsNone(row.remarks)

    async def test_an_api_holiday_for_another_category_is_not_blocked_by_a_manual_one(self):
        row = self._row(MON, "Women's Day", "MANUAL", category="GENDER")
        db, added = self._db([row])
        counts = await holiday_service.upsert_holidays(db, [(MON, "Public holiday")], source="API")
        self.assertEqual(counts, (1, 0, 0))
        self.assertEqual([(h.name, h.source) for h in added], [("Public holiday", "API")])

    async def test_an_api_batch_updates_an_earlier_api_row(self):
        row = self._row(MON, "Feed name", "API")
        db, _ = self._db([row])
        draft = holiday_service.HolidayDraft(MON, "Feed name", remarks="updated")
        self.assertEqual(await holiday_service.upsert_holidays(db, [draft], source="API"), (0, 1, 0))
        self.assertEqual(row.remarks, "updated")

    async def test_a_manual_entry_takes_over_an_api_row(self):
        row = self._row(MON, "Feed", "API")
        db, _ = self._db([row])
        await holiday_service.upsert_holidays(db, [(MON, "Feed")], source="MANUAL")
        self.assertEqual(row.source, "MANUAL")

    async def test_nothing_to_do_touches_nothing(self):
        db, _ = self._db()
        self.assertEqual(await holiday_service.upsert_holidays(db, []), (0, 0, 0))
        db.execute.assert_not_called()


class RefreshRegistryTests(HolidayTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_refresh_replaces_the_registry_with_the_table(self):
        set_configured_holidays({TUE: "stale"})
        result = mock.Mock()
        result.scalars.return_value.all.return_value = [
            Holiday(holiday_date=MON, name="A", category="ALL"),
            Holiday(holiday_date=WED, name="B", category="ALL"),
        ]
        db = SimpleNamespace(execute=mock.AsyncMock(return_value=result))
        self.assertEqual(await holiday_service.refresh_holiday_registry(db), 2)
        self.assertEqual(configured_holidays(), {MON: "A", WED: "B"})

    async def test_a_failed_refresh_keeps_the_last_loaded_holidays(self):
        set_configured_holidays({MON: "kept"})
        with mock.patch.object(holiday_service, "AsyncSessionLocal", side_effect=RuntimeError("db down")):
            await holiday_service.refresh_holiday_registry_safely()  # must not raise
        self.assertEqual(configured_holidays(), {MON: "kept"})


if __name__ == "__main__":
    unittest.main()
