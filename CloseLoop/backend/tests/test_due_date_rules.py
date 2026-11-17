import unittest
from datetime import date, timedelta

from app.models import DueAnchor, DueDateType
from app.services.due_date_rules import (
    DueDateOutOfRangeError,
    NonWorkingDayError,
    apply_due_days,
    cap_occurrence_search_end,
    due_date_window,
    max_allowed_due_date,
    max_allowed_subtask_due_date,
    resolve_generated_due_date,
    resolve_generated_subtask_due_date,
    roll_to_working_day,
    validate_due_date,
    validate_subtask_due_date,
    validate_working_day,
)

WITHIN = DueDateType.WITHIN_PERIOD
AFTER = DueDateType.AFTER_PERIOD_END

# Picnic's real P1 period (see PeriodsPage.jsx STANDARD_PERIODS) — used for
# the "a P1 task due Jan 30 is still a P1 task" scenario from the spec.
P1_START, P1_END = date(2025, 12, 29), date(2026, 1, 25)


class MaxAllowedDueDateTests(unittest.TestCase):
    def test_within_period_ceiling_is_period_end(self):
        self.assertEqual(max_allowed_due_date(P1_END, WITHIN, 0), P1_END)

    def test_within_period_ignores_post_period_days(self):
        # N is only meaningful for AFTER_PERIOD_END — WITHIN_PERIOD's
        # ceiling never moves, no matter what N is set to.
        self.assertEqual(max_allowed_due_date(P1_END, WITHIN, 30), P1_END)

    def test_after_period_end_no_longer_extends_the_ceiling(self):
        # "N days after period end" is retired: a due date stays in its period.
        self.assertEqual(max_allowed_due_date(P1_END, AFTER, 5), P1_END)

    def test_after_period_end_zero_days_equals_period_end(self):
        self.assertEqual(max_allowed_due_date(P1_END, AFTER, 0), P1_END)

    def test_negative_post_period_days_treated_as_zero(self):
        self.assertEqual(max_allowed_due_date(P1_END, AFTER, -3), P1_END)

    def test_none_post_period_days_treated_as_zero(self):
        self.assertEqual(max_allowed_due_date(P1_END, AFTER, None), P1_END)

    def test_period_is_never_mutated(self):
        # The function only ever computes a due-date ceiling — it must
        # never be mistaken for (or used to) extend the period itself.
        before = (P1_START, P1_END)
        max_allowed_due_date(P1_END, AFTER, 30)
        self.assertEqual((P1_START, P1_END), before)


class ValidateDueDateTests(unittest.TestCase):
    def test_none_due_date_always_ok(self):
        validate_due_date(
            period_start=P1_START, period_end=P1_END, due_date=None,
            due_date_type=WITHIN, post_period_due_days=0,
        )  # no raise

    def test_on_period_end_is_ok_within_period(self):
        validate_due_date(
            period_start=P1_START, period_end=P1_END, due_date=P1_END,
            due_date_type=WITHIN, post_period_due_days=0,
        )  # no raise

    def test_one_day_past_period_end_rejected_within_period(self):
        with self.assertRaises(DueDateOutOfRangeError):
            validate_due_date(
                period_start=P1_START, period_end=P1_END,
                due_date=date(2026, 1, 26),
                due_date_type=WITHIN, post_period_due_days=0,
            )

    def test_before_period_start_rejected(self):
        with self.assertRaises(DueDateOutOfRangeError):
            validate_due_date(
                period_start=P1_START, period_end=P1_END,
                due_date=date(2025, 12, 28),
                due_date_type=WITHIN, post_period_due_days=0,
            )

    def test_p1_task_due_jan_30_is_rejected_even_with_after_period_end(self):
        # P1 ends Jan 25. Jan 30 is next period's date, whatever the task asks for.
        with self.assertRaises(DueDateOutOfRangeError) as ctx:
            validate_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 30),
                due_date_type=AFTER, post_period_due_days=5,
            )
        self.assertIn("own period", str(ctx.exception))

    def test_the_old_post_period_ceiling_is_now_rejected(self):
        with self.assertRaises(DueDateOutOfRangeError):
            validate_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 30),
                due_date_type=AFTER, post_period_due_days=5,
            )

    def test_one_day_beyond_post_period_window_rejected(self):
        with self.assertRaises(DueDateOutOfRangeError):
            validate_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 31),
                due_date_type=AFTER, post_period_due_days=5,
            )

    def test_far_beyond_post_period_window_rejected(self):
        with self.assertRaises(DueDateOutOfRangeError):
            validate_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 3, 1),
                due_date_type=AFTER, post_period_due_days=5,
            )


class SubtaskValidationTests(unittest.TestCase):
    def test_subtask_within_parent_and_period_ok(self):
        validate_subtask_due_date(
            period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 20),
            due_date_type=WITHIN, post_period_due_days=0,
            parent_task_due_date=date(2026, 1, 25),
        )  # no raise

    def test_subtask_after_parent_due_date_rejected_even_if_within_period(self):
        # Sub-task date is legal for the period on its own, but later than
        # its parent task's own due date — must still be rejected.
        with self.assertRaises(DueDateOutOfRangeError):
            validate_subtask_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 24),
                due_date_type=WITHIN, post_period_due_days=0,
                parent_task_due_date=date(2026, 1, 20),
            )

    def test_subtask_past_the_period_end_is_rejected_even_under_a_late_parent(self):
        # A (legacy) parent due Jan 30 does not make Jan 28 a valid sub-task
        # date: it is still past the period's own end.
        with self.assertRaises(DueDateOutOfRangeError):
            validate_subtask_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 28),
                due_date_type=AFTER, post_period_due_days=5,
                parent_task_due_date=date(2026, 1, 30),
            )

    def test_subtask_cannot_exceed_parent_even_when_its_own_window_allows_it(self):
        # Sub-task's own AFTER_PERIOD_END window would allow Jan 30, but the
        # parent is only due Jan 28 — the parent cap wins.
        with self.assertRaises(DueDateOutOfRangeError):
            validate_subtask_due_date(
                period_start=P1_START, period_end=P1_END, due_date=date(2026, 1, 30),
                due_date_type=AFTER, post_period_due_days=5,
                parent_task_due_date=date(2026, 1, 28),
            )

    def test_max_allowed_subtask_due_date_is_the_min_of_both_ceilings(self):
        # min(period.end_date, parent_task.due_date)
        self.assertEqual(
            max_allowed_subtask_due_date(P1_END, AFTER, 5, date(2026, 1, 22)),
            date(2026, 1, 22),
        )
        self.assertEqual(
            max_allowed_subtask_due_date(P1_END, AFTER, 5, date(2026, 2, 15)), P1_END,
        )

    def test_max_allowed_subtask_due_date_no_parent_falls_back_to_own_ceiling(self):
        self.assertEqual(max_allowed_subtask_due_date(P1_END, AFTER, 5, None), P1_END)


class ResolveGeneratedDueDateTests(unittest.TestCase):
    """Template-generation resolution — the same math real Close Pack
    generation and carry-forward use (services/plan_generation_service.py,
    services/carry_forward_service.py)."""

    def test_within_period_day_n_unchanged_from_due_within(self):
        self.assertEqual(
            resolve_generated_due_date(P1_START, P1_END, 5, "PERIOD_START", WITHIN, 0),
            date(2026, 1, 2),
        )

    def test_within_period_clamps_to_period_end(self):
        # P1_END (Jan 25 2026) is a Sunday, and the next working day would
        # fall outside the period — so it settles on the Friday before.
        self.assertEqual(
            resolve_generated_due_date(P1_START, P1_END, 999, "PERIOD_START", WITHIN, 0),
            date(2026, 1, 23),
        )

    def test_after_period_end_resolves_to_the_periods_last_working_day(self):
        # A legacy "5 days after close" template stays in its period: P1 ends
        # on Sun Jan 25, so the last working day is Fri Jan 23. Its own
        # day/anchor are ignored, as they always were for this type.
        for day, anchor in ((1, "PERIOD_END"), (3, "PERIOD_START")):
            self.assertEqual(
                resolve_generated_due_date(P1_START, P1_END, day, anchor, AFTER, 5),
                date(2026, 1, 23),
            )

    def test_after_period_end_zero_days_equals_period_end(self):
        # ...unless the period end is a Sunday (Jan 25 2026): then Friday.
        self.assertEqual(
            resolve_generated_due_date(P1_START, P1_END, 1, "PERIOD_START", AFTER, 0),
            date(2026, 1, 23),
        )

    def test_after_period_end_zero_days_on_a_working_day_is_period_end(self):
        weekday_end = date(2026, 1, 23)  # Friday
        self.assertEqual(
            resolve_generated_due_date(P1_START, weekday_end, 1, "PERIOD_START", AFTER, 0),
            weekday_end,
        )

    def test_never_produces_a_date_before_period_start(self):
        d = resolve_generated_due_date(P1_START, P1_END, 1, "PERIOD_START", WITHIN, 0)
        self.assertGreaterEqual(d, P1_START)


class ResolveGeneratedSubtaskDueDateTests(unittest.TestCase):
    def test_capped_at_parent_due_date_within_period(self):
        d = resolve_generated_subtask_due_date(
            P1_START, P1_END, 999, "PERIOD_START", WITHIN, 0,
            parent_task_due_date=date(2026, 1, 10),
        )
        self.assertEqual(d, date(2026, 1, 10))

    def test_after_period_end_capped_by_parent(self):
        # Sub-task's own date would be Fri Jan 23, but the parent is due Jan 20.
        d = resolve_generated_subtask_due_date(
            P1_START, P1_END, None, "PERIOD_START", AFTER, 5,
            parent_task_due_date=date(2026, 1, 20),
        )
        self.assertEqual(d, date(2026, 1, 20))

    def test_after_period_end_not_capped_when_parent_later(self):
        d = resolve_generated_subtask_due_date(
            P1_START, P1_END, None, "PERIOD_START", AFTER, 5,
            parent_task_due_date=date(2026, 2, 15),
        )
        self.assertEqual(d, date(2026, 1, 23))

    def test_no_parent_due_date_uses_own_window(self):
        d = resolve_generated_subtask_due_date(
            P1_START, P1_END, None, "PERIOD_START", AFTER, 5,
            parent_task_due_date=None,
        )
        self.assertEqual(d, date(2026, 1, 23))


class DueDateWindowTests(unittest.TestCase):
    def test_within_period_window(self):
        w = due_date_window(P1_START, P1_END, WITHIN, 0)
        self.assertEqual(w.min_date, P1_START)
        self.assertEqual(w.max_date, P1_END)
        self.assertFalse(w.is_after_period_end)
        self.assertEqual(w.days_past_period_end, 0)

    def test_after_period_end_window_stops_at_the_period_end(self):
        w = due_date_window(P1_START, P1_END, AFTER, 5)
        self.assertEqual(w.max_date, P1_END)
        self.assertFalse(w.is_after_period_end)
        self.assertEqual(w.days_past_period_end, 0)

    def test_subtask_window_capped_by_parent(self):
        w = due_date_window(
            P1_START, P1_END, AFTER, 5, parent_task_due_date=date(2026, 1, 22)
        )
        self.assertEqual(w.max_date, date(2026, 1, 22))
        self.assertFalse(w.is_after_period_end)


class ApplyDueDaysTests(unittest.TestCase):
    """The spec's own worked example: Monthly task, Start Sep 1, Due Days
    5 -> Sep occurrence due Sep 6, Oct occurrence due Oct 6, Nov occurrence
    due Nov 6."""

    def test_monthly_example_sep(self):
        # Sep 1 + 5 = Sep 6 2026, a Sunday -> the next working day, Monday.
        self.assertEqual(apply_due_days(date(2026, 9, 1), 5), date(2026, 9, 7))

    def test_monthly_example_oct(self):
        self.assertEqual(apply_due_days(date(2026, 10, 1), 5), date(2026, 10, 6))

    def test_monthly_example_nov(self):
        self.assertEqual(apply_due_days(date(2026, 11, 1), 5), date(2026, 11, 6))

    def test_zero_due_days_is_the_occurrence_date_itself(self):
        # The pre-existing behavior for every recurring task before this
        # field existed — due_days=0 must be a complete no-op.
        self.assertEqual(apply_due_days(date(2026, 9, 1), 0), date(2026, 9, 1))

    def test_none_due_days_treated_as_zero(self):
        self.assertEqual(apply_due_days(date(2026, 9, 1), None), date(2026, 9, 1))

    def test_negative_due_days_treated_as_zero(self):
        self.assertEqual(apply_due_days(date(2026, 9, 1), -3), date(2026, 9, 1))

    def test_not_clamped_to_any_period_it_can_cross_a_month_boundary(self):
        # Occurrence lands near month-end; the due date is allowed to spill
        # into the next calendar period — N-Day Due Date is never clamped.
        # (Oct 3 2026 is a Saturday, so it rolls on to Monday Oct 5.)
        self.assertEqual(apply_due_days(date(2026, 9, 28), 5), date(2026, 10, 5))

    def test_weekday_result_is_untouched(self):
        # Mon Sep 28 + 2 = Wed Sep 30
        self.assertEqual(apply_due_days(date(2026, 9, 28), 2), date(2026, 9, 30))

    def test_friday_plus_one_day_rolls_to_monday(self):
        # The Daily/Weekly case: the occurrence is a working day, the offset
        # lands on the weekend. Fri Oct 16 2026 + 1 = Sat Oct 17 -> Mon Oct 19.
        self.assertEqual(apply_due_days(date(2026, 10, 16), 1), date(2026, 10, 19))

    def test_saturday_occurrence_with_zero_due_days_rolls_to_monday(self):
        # A Monthly/Quarterly/Annual occurrence on a Saturday (Oct 17 2026).
        self.assertEqual(apply_due_days(date(2026, 10, 17), 0), date(2026, 10, 19))

    def test_sunday_occurrence_rolls_to_monday(self):
        self.assertEqual(apply_due_days(date(2026, 10, 18), None), date(2026, 10, 19))


class CapOccurrenceSearchEndTests(unittest.TestCase):
    def test_shifts_the_search_window_back_by_due_days(self):
        # A sub-task's own occurrence must land on/before (range_end -
        # due_days) so that occurrence + due_days still lands on/before
        # range_end (typically the parent task's due date).
        self.assertEqual(cap_occurrence_search_end(date(2026, 1, 25), 5), date(2026, 1, 20))

    def test_zero_due_days_is_a_no_op(self):
        self.assertEqual(cap_occurrence_search_end(date(2026, 1, 25), 0), date(2026, 1, 25))

    def test_round_trips_with_apply_due_days(self):
        # range_end is a working day (Fri Jan 23 2026) — a weekend one would
        # roll forward past it, which is why a sub-task's parent due date
        # (always a working day) is the only bound passed in practice.
        range_end = date(2026, 1, 23)
        due_days = 5
        latest_occurrence = cap_occurrence_search_end(range_end, due_days)
        self.assertEqual(apply_due_days(latest_occurrence, due_days), range_end)

    def test_an_occurrence_within_the_cap_never_rolls_past_a_working_day_range_end(self):
        range_end = date(2026, 1, 23)  # Friday
        due_days = 2
        for back in range(0, 14):
            occurrence = cap_occurrence_search_end(range_end, due_days) - timedelta(days=back)
            self.assertLessEqual(apply_due_days(occurrence, due_days), range_end, occurrence)


class RollToWorkingDayTests(unittest.TestCase):
    """2026-10-16 is a Friday, so 17 = Saturday, 18 = Sunday, 19 = Monday."""

    FRI, SAT, SUN, MON = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)

    def test_a_working_day_is_returned_unchanged(self):
        self.assertEqual(roll_to_working_day(self.FRI), self.FRI)
        self.assertEqual(roll_to_working_day(self.MON, prefer_later=False), self.MON)

    def test_saturday_and_sunday_roll_to_monday_by_default(self):
        self.assertEqual(roll_to_working_day(self.SAT), self.MON)
        self.assertEqual(roll_to_working_day(self.SUN), self.MON)

    def test_prefer_earlier_rolls_to_the_friday_before(self):
        self.assertEqual(roll_to_working_day(self.SAT, prefer_later=False), self.FRI)
        self.assertEqual(roll_to_working_day(self.SUN, prefer_later=False), self.FRI)

    def test_upper_bound_forces_the_earlier_day(self):
        self.assertEqual(roll_to_working_day(self.SAT, hi=self.SUN), self.FRI)

    def test_lower_bound_forces_the_later_day(self):
        self.assertEqual(roll_to_working_day(self.SUN, lo=self.SAT, prefer_later=False), self.MON)

    def test_a_window_with_no_working_day_returns_the_date_as_is(self):
        self.assertEqual(roll_to_working_day(self.SAT, lo=self.SAT, hi=self.SUN), self.SAT)


class ResolveGeneratedDueDateWeekendTests(unittest.TestCase):
    """A "Day N of the period" due date is never left on a Saturday/Sunday,
    and never leaves the window its due_date_type allows."""

    # Oct 1 2025 is a Wednesday: Oct 4 = Sat, Oct 5 = Sun, Oct 6 = Mon.
    S, E = date(2025, 10, 1), date(2025, 10, 31)  # E = Friday
    # Same start, but the period ends on Sunday Nov 2 2025.
    E_SUN = date(2025, 11, 2)

    def test_saturday_day_n_rolls_forward_to_monday(self):
        self.assertEqual(
            resolve_generated_due_date(self.S, self.E, 4, "PERIOD_START", WITHIN, 0),
            date(2025, 10, 6),
        )

    def test_sunday_day_n_rolls_forward_to_monday(self):
        self.assertEqual(
            resolve_generated_due_date(self.S, self.E, 5, "PERIOD_START", WITHIN, 0),
            date(2025, 10, 6),
        )

    def test_working_day_n_is_untouched(self):
        self.assertEqual(
            resolve_generated_due_date(self.S, self.E, 6, "PERIOD_START", WITHIN, 0),
            date(2025, 10, 6),
        )

    def test_period_start_anchor_falls_back_to_friday_when_monday_is_past_period_end(self):
        self.assertEqual(
            resolve_generated_due_date(self.S, self.E_SUN, 999, "PERIOD_START", WITHIN, 0),
            date(2025, 10, 31),
        )

    def test_period_end_anchor_rolls_back_to_friday_never_forward(self):
        # "N days from close" must not slip closer to the close: day 1 and
        # day 2 (Sun Nov 2, Sat Nov 1) both settle on Fri Oct 31.
        for n in (1, 2, 3):
            self.assertEqual(
                resolve_generated_due_date(self.S, self.E_SUN, n, "PERIOD_END", WITHIN, 0),
                date(2025, 10, 31),
                n,
            )

    def test_period_end_anchor_mid_month_weekend_goes_to_the_earlier_friday(self):
        # Period ends Wed Oct 29; day 4 = Sun Oct 26 -> Fri Oct 24, not Mon
        # Oct 27 (which would be only 2 days from close instead of 3).
        self.assertEqual(
            resolve_generated_due_date(self.S, date(2025, 10, 29), 4, "PERIOD_END", WITHIN, 0),
            date(2025, 10, 24),
        )

    def test_anchor_enum_behaves_like_the_string(self):
        # The close-pack validators pass DueAnchor straight off the ORM row.
        for n in (1, 2, 3, 4):
            self.assertEqual(
                resolve_generated_due_date(self.S, self.E_SUN, n, DueAnchor.PERIOD_END, WITHIN, 0),
                resolve_generated_due_date(self.S, self.E_SUN, n, "PERIOD_END", WITHIN, 0),
                n,
            )
            self.assertEqual(
                resolve_generated_due_date(self.S, self.E_SUN, n, DueAnchor.PERIOD_START, WITHIN, 0),
                resolve_generated_due_date(self.S, self.E_SUN, n, "PERIOD_START", WITHIN, 0),
                n,
            )

    def test_after_period_end_on_a_weekend_period_end_rolls_back_to_friday(self):
        # P1_END is Sun Jan 25 2026 -> Fri Jan 23, whatever N is.
        self.assertEqual(
            resolve_generated_due_date(P1_START, P1_END, 1, "PERIOD_START", AFTER, 6),
            date(2026, 1, 23),
        )

    def test_after_period_end_on_a_working_day_period_end_is_that_day(self):
        # A period that ends on a Monday: the last working day is the period end.
        end = date(2026, 2, 23)  # Monday
        self.assertEqual(
            resolve_generated_due_date(date(2026, 1, 26), end, 1, "PERIOD_START", AFTER, 1),
            end,
        )

    def test_roll_non_working_false_returns_the_raw_date(self):
        self.assertEqual(
            resolve_generated_due_date(self.S, self.E, 4, "PERIOD_START", WITHIN, 0,
                                       roll_non_working=False),
            date(2025, 10, 4),
        )
        self.assertEqual(
            resolve_generated_due_date(P1_START, P1_END, 1, "PERIOD_START", AFTER, 0,
                                       roll_non_working=False),
            P1_END,
        )

    def test_every_day_n_lands_on_a_working_day_inside_the_period(self):
        for end in (self.E, self.E_SUN):
            for anchor in ("PERIOD_START", "PERIOD_END"):
                for n in range(1, 45):
                    d = resolve_generated_due_date(self.S, end, n, anchor, WITHIN, 0)
                    self.assertNotIn(d.weekday(), (5, 6), (end, anchor, n, d))
                    self.assertTrue(self.S <= d <= end, (end, anchor, n, d))

    def test_a_sub_task_template_is_still_capped_at_its_parent(self):
        # Sub-task day 999 -> period end Sun Jan 25 -> rolled to Fri Jan 23,
        # and the parent is only due Thu Jan 22.
        d = resolve_generated_subtask_due_date(
            P1_START, P1_END, 999, "PERIOD_START", WITHIN, 0,
            parent_task_due_date=date(2026, 1, 22),
        )
        self.assertEqual(d, date(2026, 1, 22))


class ValidateWorkingDayTests(unittest.TestCase):
    """A user-picked due date can't be a Saturday/Sunday. 2026-10-16 is a
    Friday, so 17 = Saturday, 18 = Sunday, 19 = Monday."""

    FRI, SAT, SUN, MON = date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)

    def test_saturday_is_rejected(self):
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(self.SAT)
        self.assertIn("Saturday", str(ctx.exception))
        self.assertIn("2026-10-17", str(ctx.exception))

    def test_sunday_is_rejected(self):
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(self.SUN)
        self.assertIn("Sunday", str(ctx.exception))

    def test_monday_to_friday_are_fine(self):
        for offset in range(5):
            validate_working_day(date(2026, 10, 12 + offset))  # Mon 12 .. Fri 16

    def test_no_date_is_fine(self):
        validate_working_day(None)

    def test_label_prefixes_the_message(self):
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(self.SAT, label="Sub-task")
        self.assertTrue(str(ctx.exception).startswith("Sub-task due date"))

    def test_resubmitting_the_stored_weekend_date_is_allowed(self):
        # A task that predates the rule keeps saving when something else
        # (title, status, ...) is edited and the client echoes its date back.
        validate_working_day(self.SAT, current=self.SAT)

    def test_changing_to_a_different_weekend_date_is_rejected(self):
        with self.assertRaises(NonWorkingDayError):
            validate_working_day(self.SUN, current=self.SAT)

    def test_moving_a_legacy_weekend_date_to_a_weekday_is_fine(self):
        validate_working_day(self.MON, current=self.SAT)

    def test_is_caught_by_the_existing_out_of_range_handlers(self):
        # Routers already turn DueDateOutOfRangeError into a 400.
        self.assertTrue(issubclass(NonWorkingDayError, DueDateOutOfRangeError))


if __name__ == "__main__":
    unittest.main()
