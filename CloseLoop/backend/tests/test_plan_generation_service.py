import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from app.models import CalendarType as C, DueAnchor, RecurrenceFrequency as F
from app.calendar import bs_month_length, format_bs, to_ad, to_bs
from app.services import plan_generation_service as pgs
from app.services.plan_generation_service import _subtask_dates, preview_tasks_for_period
from app.services.recurrence import due_within, occurrence_dates


def occ(**kw):
    base = dict(interval_count=1, days_of_week=None, end=None)
    base.update(kw)
    return occurrence_dates(**base)


class DueWithinTests(unittest.TestCase):
    S, E = date(2025, 10, 1), date(2025, 10, 31)

    def test_day_offset(self):
        self.assertEqual(due_within(self.S, self.E, 10), date(2025, 10, 10))

    def test_clamps_to_end(self):
        self.assertEqual(due_within(self.S, self.E, 99), date(2025, 10, 31))

    def test_none_is_day_one(self):
        self.assertEqual(due_within(self.S, self.E, None), date(2025, 10, 1))

    def test_anchor_period_end_day_1_is_last_day(self):
        self.assertEqual(due_within(self.S, self.E, 1, "PERIOD_END"), date(2025, 10, 31))

    def test_anchor_period_end_counts_back(self):
        # "3 days from close" -> day 4 from the end
        self.assertEqual(due_within(self.S, self.E, 4, "PERIOD_END"), date(2025, 10, 28))

    def test_anchor_enum_is_read_like_its_string(self):
        # Straight off an ORM row the anchor is a DueAnchor, not a str.
        self.assertEqual(due_within(self.S, self.E, 4, DueAnchor.PERIOD_END), date(2025, 10, 28))
        self.assertEqual(due_within(self.S, self.E, 4, DueAnchor.PERIOD_START), date(2025, 10, 4))

    def test_anchor_period_end_clamps_to_start(self):
        self.assertEqual(due_within(self.S, self.E, 999, "PERIOD_END"), date(2025, 10, 1))


class OccurrenceDatesTests(unittest.TestCase):
    RS, RE = date(2025, 10, 1), date(2025, 12, 31)  # 3-month range

    def test_no_frequency_is_empty(self):
        self.assertEqual(occ(frequency=None, start=date(2025, 10, 1), range_start=self.RS, range_end=self.RE), [])

    def test_daily_every_3_from_start(self):
        d = occ(frequency=F.DAILY, interval_count=3, start=date(2025, 10, 2),
                range_start=self.RS, range_end=date(2025, 10, 15))
        # The raw every-3-days series is Thu 2, Sun 5, Wed 8, Sat 11, Tue 14;
        # Sun 5 and Sat 11 are weekend days and move to the next working day
        # (Mon 6, Mon 13) — they are not dropped.
        self.assertEqual(d, [date(2025, 10, 2), date(2025, 10, 6), date(2025, 10, 8),
                             date(2025, 10, 13), date(2025, 10, 14)])

    def test_daily_skips_before_start(self):
        # Fri 10 is the only working day in the range (Sat 11 / Sun 12 drop).
        d = occ(frequency=F.DAILY, interval_count=1, start=date(2025, 10, 10),
                range_start=date(2025, 10, 1), range_end=date(2025, 10, 12))
        self.assertEqual(d, [date(2025, 10, 10)])

    def test_daily_never_lands_on_saturday_or_sunday(self):
        d = occ(frequency=F.DAILY, interval_count=1, start=date(2025, 10, 1),
                range_start=date(2025, 10, 1), range_end=date(2025, 10, 31))
        self.assertTrue(d)
        self.assertTrue(all(x.weekday() < 5 for x in d))
        # Oct 2025 has 23 weekdays.
        self.assertEqual(len(d), 23)

    def test_daily_weekend_start_first_occurrence_is_next_working_day(self):
        # start = Sat 2025-10-04: that day is dropped, Mon 6 is the first hit.
        d = occ(frequency=F.DAILY, interval_count=1, start=date(2025, 10, 4),
                range_start=self.RS, range_end=date(2025, 10, 8))
        self.assertEqual(d, [date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8)])

    def test_weekly_drops_saturday_and_sunday_from_days_of_week(self):
        # Legacy template / bulk import that still carries Sat(5) + Sun(6).
        d = occ(frequency=F.WEEKLY, interval_count=1, days_of_week=[0, 5, 6],
                start=date(2025, 10, 6), range_start=self.RS, range_end=date(2025, 10, 19))
        self.assertEqual(d, [date(2025, 10, 6), date(2025, 10, 13)])

    def test_weekly_weekend_only_produces_nothing(self):
        d = occ(frequency=F.WEEKLY, interval_count=1, days_of_week=[5, 6],
                start=date(2025, 10, 6), range_start=self.RS, range_end=self.RE)
        self.assertEqual(d, [])

    def test_monthly_is_not_filtered_by_weekend(self):
        # Period-level cadences keep their raw occurrence date: Sat 2025-11-15.
        d = occ(frequency=F.MONTHLY, start=date(2025, 10, 15), range_start=self.RS, range_end=self.RE)
        self.assertIn(date(2025, 11, 15), d)

    def test_weekly_monday_biweekly(self):
        # start Mon 2025-10-06. Mondays: 6 (wk0 fire), 13 (wk1 skip), 20 (wk2 fire), 27 (wk3 skip)
        d = occ(frequency=F.WEEKLY, interval_count=2, days_of_week=[0], start=date(2025, 10, 6),
                range_start=self.RS, range_end=date(2025, 10, 31))
        self.assertEqual(d, [date(2025, 10, 6), date(2025, 10, 20)])

    def test_monthly_on_start_day(self):
        d = occ(frequency=F.MONTHLY, start=date(2025, 10, 15), range_start=self.RS, range_end=self.RE)
        self.assertEqual(d, [date(2025, 10, 15), date(2025, 11, 15), date(2025, 12, 15)])

    def test_quarterly_every_3_months(self):
        d = occ(frequency=F.QUARTERLY, start=date(2025, 10, 20),
                range_start=self.RS, range_end=date(2026, 12, 31))
        self.assertEqual(d, [date(2025, 10, 20), date(2026, 1, 20), date(2026, 4, 20),
                             date(2026, 7, 20), date(2026, 10, 20)])

    def test_monthly_custom_every_2_months(self):
        # "Custom" = MONTHLY with interval_count 2 -> every other month.
        d = occ(frequency=F.MONTHLY, interval_count=2, start=date(2025, 10, 10),
                range_start=self.RS, range_end=date(2026, 4, 30))
        self.assertEqual(d, [date(2025, 10, 10), date(2025, 12, 10),
                             date(2026, 2, 10), date(2026, 4, 10)])

    def test_quarterly_custom_every_2_quarters(self):
        # QUARTERLY x2 == every 6 months.
        d = occ(frequency=F.QUARTERLY, interval_count=2, start=date(2025, 1, 15),
                range_start=date(2025, 1, 1), range_end=date(2026, 1, 31))
        self.assertEqual(d, [date(2025, 1, 15), date(2025, 7, 15), date(2026, 1, 15)])

    def test_annual(self):
        d = occ(frequency=F.ANNUAL, start=date(2025, 12, 31),
                range_start=self.RS, range_end=date(2028, 1, 1))
        self.assertEqual(d, [date(2025, 12, 31), date(2026, 12, 31), date(2027, 12, 31)])

    def test_recurrence_end_caps_it(self):
        d = occ(frequency=F.MONTHLY, start=date(2025, 10, 15), end=date(2025, 11, 30),
                range_start=self.RS, range_end=self.RE)
        self.assertEqual(d, [date(2025, 10, 15), date(2025, 11, 15)])

    def test_month_day_clamped_for_short_month(self):
        # 31st -> Feb clamps to 28 (2026 not leap)
        d = occ(frequency=F.MONTHLY, start=date(2026, 1, 31),
                range_start=date(2026, 1, 1), range_end=date(2026, 3, 31))
        self.assertEqual(d, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])


class BSCalendarSteppingTests(unittest.TestCase):
    def test_bs_annual_keeps_the_nepali_date(self):
        anchor = date(2025, 7, 17)  # ~ 1 Shrawan 2082
        self.assertEqual((to_bs(anchor).month, to_bs(anchor).day), (4, 1))  # Shrawan 1
        ds = occ(frequency=F.ANNUAL, start=anchor, end=None,
                 range_start=anchor, range_end=date(2029, 1, 1), calendar=C.BS)
        # every occurrence is Shrawan 1 on the Nepali calendar
        for d in ds:
            bs = to_bs(d)
            self.assertEqual((bs.month, bs.day), (4, 1), format_bs(bs, "short"))
        self.assertGreaterEqual(len(ds), 3)

    def test_ad_annual_drifts_on_bs_calendar(self):
        anchor = date(2025, 7, 17)
        ds = occ(frequency=F.ANNUAL, start=anchor, end=None,
                 range_start=anchor, range_end=date(2029, 1, 1), calendar=C.AD)
        # AD stepping keeps the AD date fixed; BS day is NOT always 1
        bs_days = {to_bs(d).day for d in ds}
        self.assertNotEqual(bs_days, {1})

    def test_bs_quarterly_steps_three_bs_months(self):
        anchor = date(2025, 7, 17)  # Shrawan 1
        ds = occ(frequency=F.QUARTERLY, start=anchor, end=None,
                 range_start=anchor, range_end=date(2026, 12, 31), calendar=C.BS)
        months = [to_bs(d).month for d in ds]
        # Shrawan(4) -> Kartik(7) -> Magh(10) -> Baisakh(1) ...
        self.assertEqual(months[:3], [4, 7, 10])
        self.assertTrue(all(to_bs(d).day == 1 for d in ds))


class RecurrenceEndCutoffTests(unittest.TestCase):
    """A task's recurrence_end is a hard cutoff: once it falls inside
    Poush, no occurrence of ANY frequency may land in Magh or any later
    period — the exact scenario reported against the Lead Dashboard's
    Upcoming Tasks. `_materialize_recurring` / `preview_tasks_for_period`
    both find "the next occurrence" via
    `next((d for d in all_occ if d >= period.start_date), None)` over a
    generous tail window — these tests reproduce that exact search against
    `occurrence_dates` (the shared primitive both call), so a regression
    here is a regression in the real generation/preview code too."""

    POUSH_START = to_ad(2082, 9, 1)
    POUSH_END = to_ad(2082, 9, bs_month_length(2082, 9))
    MAGH_START = to_ad(2082, 10, 1)
    TAIL = date(2030, 1, 1)  # mirrors the "+2000 days" tail search window

    def _next_on_or_after_magh(self, **kw):
        base = dict(
            interval_count=1, days_of_week=None,
            range_start=self.POUSH_START, range_end=self.TAIL,
        )
        base.update(kw)
        dates = occurrence_dates(**base)
        return next((d for d in dates if d >= self.MAGH_START), None)

    def test_daily_ends_in_poush(self):
        nxt = self._next_on_or_after_magh(
            frequency=F.DAILY, start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_weekly_ends_in_poush(self):
        nxt = self._next_on_or_after_magh(
            frequency=F.WEEKLY, days_of_week=[0],
            start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_monthly_ends_in_poush(self):
        nxt = self._next_on_or_after_magh(
            frequency=F.MONTHLY, start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_quarterly_ends_in_poush(self):
        nxt = self._next_on_or_after_magh(
            frequency=F.QUARTERLY, start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_semi_annual_ends_in_poush(self):
        nxt = self._next_on_or_after_magh(
            frequency=F.SEMI_ANNUAL, start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_annual_ends_in_poush(self):
        nxt = self._next_on_or_after_magh(
            frequency=F.ANNUAL, start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_custom_every_2_months_ends_in_poush(self):
        # "Custom" cadence is a base frequency + interval_count > 1 — same
        # primitive, no separate code path to regress.
        nxt = self._next_on_or_after_magh(
            frequency=F.MONTHLY, interval_count=2,
            start=self.POUSH_START, end=self.POUSH_END,
        )
        self.assertIsNone(nxt)

    def test_no_end_date_keeps_recurring_past_magh(self):
        # Negative case: without an end date the cutoff must not apply —
        # existing recurring behavior is unchanged.
        nxt = self._next_on_or_after_magh(
            frequency=F.MONTHLY, start=self.POUSH_START, end=None,
        )
        self.assertIsNotNone(nxt)
        self.assertGreaterEqual(nxt, self.MAGH_START)


def _period(start, end):
    return SimpleNamespace(start_date=start, end_date=end)


def _tmpl(**kw):
    base = dict(title="T", interval_count=1, days_of_week=None, due_days=0, sub_task_templates=[])
    base.update(kw)
    return base


class GeneratedDueDatesAvoidWeekendsTests(unittest.TestCase):
    """What a generated task is DUE (Saturday/Sunday rolled to a working day)
    versus which OCCURRENCE it is (the raw landing date — identity, period
    membership, title suffix — never rolled). 2026-10-16 is a Friday."""

    def test_monthly_occurrence_on_a_saturday_is_due_the_next_monday(self):
        rows = preview_tasks_for_period(
            [_tmpl(frequency="MONTHLY", recurrence_start=date(2026, 10, 17))],
            _period(date(2026, 10, 1), date(2026, 10, 31)),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 10, 17))  # stays raw
        self.assertEqual(rows[0]["due_date"], date(2026, 10, 19))

    def test_monthly_occurrence_on_a_working_day_is_unchanged(self):
        rows = preview_tasks_for_period(
            [_tmpl(frequency="MONTHLY", recurrence_start=date(2026, 10, 14))],
            _period(date(2026, 10, 1), date(2026, 10, 31)),
        )
        self.assertEqual(rows[0]["due_date"], date(2026, 10, 14))

    def test_annual_occurrence_on_a_sunday_is_due_the_next_monday(self):
        rows = preview_tasks_for_period(
            [_tmpl(frequency="ANNUAL", recurrence_start=date(2026, 10, 18))],
            _period(date(2026, 10, 1), date(2026, 10, 31)),
        )
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 10, 18))
        self.assertEqual(rows[0]["due_date"], date(2026, 10, 19))

    def test_daily_due_days_that_land_on_the_weekend_stay_inside_the_period(self):
        rows = preview_tasks_for_period(
            [_tmpl(frequency="DAILY", recurrence_start=date(2026, 10, 12), due_days=1)],
            _period(date(2026, 10, 12), date(2026, 10, 18)),
        )
        by_occ = {r["occurrence_date"]: r["due_date"] for r in rows}
        self.assertEqual(sorted(by_occ), [date(2026, 10, d) for d in (12, 13, 14, 15, 16)])
        self.assertEqual(by_occ[date(2026, 10, 15)], date(2026, 10, 16))
        # Friday + 1 day = Saturday. Monday Oct 19 opens the NEXT period, so the
        # date goes back to Friday instead of leaving this one.
        self.assertEqual(by_occ[date(2026, 10, 16)], date(2026, 10, 16))
        self.assertTrue(all(d.weekday() < 5 for d in by_occ.values()))
        self.assertTrue(all(date(2026, 10, 12) <= d <= date(2026, 10, 18) for d in by_occ.values()))

    def test_daily_due_days_mid_period_weekend_still_rolls_forward_to_monday(self):
        # Fri Oct 16 + 1 = Sat; this period runs to Oct 31, so Monday Oct 19 is inside it.
        rows = preview_tasks_for_period(
            [_tmpl(frequency="DAILY", recurrence_start=date(2026, 10, 12), due_days=1)],
            _period(date(2026, 10, 12), date(2026, 10, 31)),
        )
        by_occ = {r["occurrence_date"]: r["due_date"] for r in rows}
        self.assertEqual(by_occ[date(2026, 10, 16)], date(2026, 10, 19))

    def test_placeholder_next_due_date_is_rolled_too(self):
        # Quarterly from Sat Oct 17 2026; November has no occurrence, so the
        # row names the next one: Sun Jan 17 2027 -> due Mon Jan 18.
        rows = preview_tasks_for_period(
            [_tmpl(frequency="QUARTERLY", recurrence_start=date(2026, 10, 17))],
            _period(date(2026, 11, 1), date(2026, 11, 30)),
        )
        self.assertFalse(rows[0]["occurs_in_period"])
        self.assertEqual(rows[0]["next_due_date"], date(2027, 1, 18))

    def test_once_per_period_day_n_on_a_weekend_rolls_to_monday(self):
        # Oct 1 2025 is a Wednesday; day 4 = Sat Oct 4 -> Mon Oct 6.
        rows = preview_tasks_for_period(
            [_tmpl(due_day_of_period=4, due_anchor="PERIOD_START")],
            _period(date(2025, 10, 1), date(2025, 10, 31)),
        )
        self.assertEqual(rows[0]["due_date"], date(2025, 10, 6))


class OncePerPeriodIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    """`_materialize_once_per_period` must not add a second copy of a task a
    previous run already generated — including one generated BEFORE weekend
    due dates were rolled, which sits on the raw (Saturday) date."""

    PERIOD = SimpleNamespace(id="p1", start_date=date(2025, 10, 1), end_date=date(2025, 10, 31))
    TMPL = {"id": "tpl1", "title": "Bank rec", "due_day_of_period": 4,
            "due_anchor": "PERIOD_START", "sub_task_templates": []}
    RAW, ROLLED = date(2025, 10, 4), date(2025, 10, 6)  # Saturday, Monday

    async def _run(self, existing_keys):
        added = []
        db = SimpleNamespace(add=added.append, flush=mock.AsyncMock())
        with mock.patch.object(pgs, "_get_or_create_plan", mock.AsyncMock(return_value=SimpleNamespace(id="plan1"))), \
                mock.patch.object(pgs, "_existing_keys", mock.AsyncMock(return_value=existing_keys)):
            await pgs._materialize_once_per_period(
                db, tenant_id="t1", window=[self.PERIOD], current_end=self.PERIOD.end_date,
                entity_id="e1", owner_id="u1", t_tmpl=self.TMPL, pack=None,
                version_number=None, plan_cache={},
            )
        return added

    async def test_generates_the_task_on_the_rolled_date(self):
        added = await self._run(set())
        self.assertEqual([t.due_date for t in added], [self.ROLLED])

    async def test_a_task_already_on_the_rolled_date_is_not_duplicated(self):
        self.assertEqual(await self._run({("tpl1", None, self.ROLLED)}), [])

    async def test_a_task_generated_before_rolling_existed_is_not_duplicated(self):
        self.assertEqual(await self._run({("tpl1", None, self.RAW)}), [])

    async def test_another_templates_task_does_not_block_it(self):
        added = await self._run({("other", None, self.RAW), ("other", None, self.ROLLED)})
        self.assertEqual(len(added), 1)


class RecurringSubtaskDatesTests(unittest.TestCase):
    """A recurring sub-task's due dates roll off weekends but never past the
    parent task's own due date."""

    PERIOD = _period(date(2026, 10, 12), date(2026, 10, 25))  # Mon .. Sun

    def _dates(self, parent_due, **kw):
        s = dict(frequency="DAILY", interval_count=1, days_of_week=None,
                 recurrence_start=date(2026, 10, 12), due_days=1)
        s.update(kw)
        return _subtask_dates(s, parent_due, self.PERIOD)

    def test_rolled_due_date_can_land_exactly_on_a_monday_parent(self):
        pairs = self._dates(date(2026, 10, 19))  # parent due Monday
        self.assertEqual(pairs[-1], (date(2026, 10, 16), date(2026, 10, 19)))
        self.assertTrue(all(due <= date(2026, 10, 19) for _, due in pairs))

    def test_never_exceeds_a_parent_that_was_left_on_a_weekend(self):
        # Parent Sat Oct 17: Friday's occurrence + 1 day would roll to Mon
        # Oct 19, past the parent — capped at the parent's date instead.
        pairs = self._dates(date(2026, 10, 17))
        self.assertTrue(all(due <= date(2026, 10, 17) for _, due in pairs))
        self.assertEqual(pairs[-1], (date(2026, 10, 16), date(2026, 10, 17)))


if __name__ == "__main__":
    unittest.main()
