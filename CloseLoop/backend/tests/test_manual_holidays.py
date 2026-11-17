"""The manually maintained holidays (2083 BS) and how they join the weekend rule.

Covers the seeded list itself (migration 0056), the registry that carries it
(global ALL holidays vs. gender / religion ones), the shared working-day
helpers, due-date shifting, recurrence, and the request validation. Nothing
here needs a database; the migration, restart and re-run checks against a real
Postgres are run separately.
"""

import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pydantic import ValidationError

from app.calendar import (
    FiscalYear,
    HolidayEntry,
    Period,
    StaffProfile,
    bs_iso,
    classify_day,
    configured_holidays,
    fiscal_year_holiday_events,
    holiday_skips_everyone,
    is_holiday_for,
    is_non_working_day,
    known_holidays,
    last_working_day,
    nepal_holidays,
    next_working_day,
    previous_working_day,
    scoped_holidays_on,
    set_configured_holidays,
    set_holiday_entries,
    to_ad,
    working_day_count,
    working_days_between,
)
from app.calendar import holiday_registry, nepali_calendar
from app.models import Holiday, RecurrenceFrequency as F
from app.schemas import HolidayIn, HolidayOut
from app.services import holiday_service
from app.services.due_date_rules import (
    NonWorkingDayError,
    apply_due_days,
    roll_to_working_day,
    validate_working_day,
)
from app.services.recurrence import occurrence_dates

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0056_manual_holidays_2083.py"
_spec = importlib.util.spec_from_file_location("migration_0056", MIGRATION)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _entries():
    """The seeded list as registry entries — what refresh_holiday_registry
    builds from the table after the migration."""
    return [HolidayEntry(ad, name, cat, aud) for name, _bs, ad, cat, aud, _rem in migration.HOLIDAYS_2083]


def d(text):
    return date.fromisoformat(text)


class ManualHolidayTestCase(unittest.TestCase):
    """Every test starts with an empty registry and leaves it that way."""

    def setUp(self):
        set_configured_holidays({})
        self.addCleanup(set_configured_holidays, {})

    def load_2083(self):
        set_holiday_entries(_entries())


class SeededListTests(unittest.TestCase):
    def test_fourteen_rows_were_supplied_and_thirteen_holidays_are_stored(self):
        self.assertEqual(len(migration.SUPPLIED_2083), 14)
        self.assertEqual(len(migration.HOLIDAYS_2083), 13)

    def test_christmas_is_stored_once_not_twice(self):
        christmas = [h for h in migration.HOLIDAYS_2083 if h[0] == "Christmas"]
        self.assertEqual(len(christmas), 1)
        self.assertEqual(christmas[0][2], date(2026, 12, 25))

    def test_every_holiday_is_unique_by_date_and_name(self):
        keys = [(h[2], h[0].casefold()) for h in migration.HOLIDAYS_2083]
        self.assertEqual(len(keys), len(set(keys)))

    def test_names_dates_and_remarks_are_exactly_as_supplied(self):
        by_name = {h[0]: h for h in migration.HOLIDAYS_2083}
        self.assertEqual(
            {n: (h[1], h[2].isoformat(), h[3], h[5]) for n, h in by_name.items()},
            {
                "Tihar-Laxmi Puja": ("2083-07-22", "2026-11-08", "ALL", "Tihar Holiday"),
                "Tihar-Gobardhan Puja": ("2083-07-24", "2026-11-10", "ALL", "Tihar Holiday"),
                "Tihar-Bhaitika": ("2083-07-25", "2026-11-11", "ALL", "Tihar Holiday"),
                "Maha Shivaratri": ("2083-11-22", "2027-03-06", "ALL", "Shivaratri"),
                "Teej": ("2083-05-29", "2026-09-14", "GENDER", "Only for female staff"),
                "International Women's Day": ("2083-11-24", "2027-03-08", "GENDER", "Only for female staff"),
                "Christmas": ("2083-09-10", "2026-12-25", "RELIGION", "Only for Christians"),
                "Holi": ("2083-12-07", "2027-03-21", "ALL", "Holi"),
                "English New Year": ("2083-09-17", "2027-01-01", "ALL", "Happy New Year"),
                "Dashain-Fulpati": ("2083-06-31", "2026-10-17", "ALL", "Dashain Holiday"),
                "Dashain-Ekadashi": ("2083-07-05", "2026-10-22", "ALL", "Dashain Holiday"),
                "Dashain-Dashami": ("2083-07-04", "2026-10-21", "ALL", "Dashain Holiday"),
                "Dashain-Astami": ("2083-07-01", "2026-10-18", "ALL", "Dashain Holiday"),
            },
        )

    def test_every_bs_date_matches_its_ad_date_in_the_projects_own_converter(self):
        for name, bs, ad, *_ in migration.HOLIDAYS_2083:
            y, m, day = (int(p) for p in bs.split("-"))
            self.assertEqual(to_ad(y, m, day), ad, name)
            self.assertEqual(bs_iso(ad), bs, name)

    def test_every_holiday_is_in_the_year_2083_bs(self):
        self.assertTrue(all(h[1].startswith("2083-") for h in migration.HOLIDAYS_2083))

    def test_scoped_holidays_say_who_they_are_for_and_global_ones_do_not(self):
        for name, _bs, _ad, category, audience, _rem in migration.HOLIDAYS_2083:
            self.assertIn(category, ("ALL", "GENDER", "RELIGION"), name)
            self.assertEqual(bool(audience), category != "ALL", name)
        audiences = {h[0]: h[4] for h in migration.HOLIDAYS_2083 if h[3] != "ALL"}
        self.assertEqual(audiences, {"Teej": "FEMALE", "International Women's Day": "FEMALE", "Christmas": "CHRISTIAN"})

    def test_deduping_the_supplied_rows_twice_changes_nothing(self):
        once = migration._unique(migration.SUPPLIED_2083)
        self.assertEqual(migration._unique(once), once)
        self.assertEqual(migration._unique(migration.SUPPLIED_2083 + migration.SUPPLIED_2083), once)

    def test_the_migration_chains_after_0055_and_inserts_idempotently(self):
        self.assertEqual(migration.down_revision, "0055_due_dates_stay_in_period")
        self.assertLessEqual(len(migration.revision), 32)
        source = MIGRATION.read_text()
        self.assertIn("ON CONFLICT (holiday_date, name) DO NOTHING", source)

    def test_the_model_allows_several_holidays_on_one_date_but_not_a_repeat(self):
        constraints = {c.name: [col.name for col in c.columns]
                       for c in Holiday.__table__.constraints if c.name and c.name.startswith("uq_")}
        self.assertEqual(constraints, {"uq_holidays_date_name": ["holiday_date", "name"]})


class WorkingDayTests(ManualHolidayTestCase):
    def setUp(self):
        super().setUp()
        self.load_2083()

    def nwd(self, text):
        return next_working_day(d(text)).isoformat()

    # 1 / 2
    def test_a_normal_working_day_is_unchanged(self):
        self.assertEqual(self.nwd("2026-10-19"), "2026-10-19")  # Monday
        self.assertEqual(self.nwd("2026-11-12"), "2026-11-12")  # Thursday after Tihar

    def test_a_plain_weekend_behaves_exactly_as_before(self):
        self.assertEqual(self.nwd("2026-10-24"), "2026-10-26")  # Sat -> Mon
        self.assertEqual(self.nwd("2026-10-25"), "2026-10-26")  # Sun -> Mon
        self.assertTrue(is_non_working_day(d("2026-10-24")) and is_non_working_day(d("2026-10-25")))
        self.assertFalse(is_non_working_day(d("2026-10-26")))

    # 3
    def test_a_due_date_on_a_holiday_moves_to_the_next_working_day(self):
        self.assertEqual(self.nwd("2026-11-10"), "2026-11-12")  # Tihar Tue, Wed also a holiday
        self.assertEqual(self.nwd("2027-01-01"), "2027-01-04")  # New Year Fri, weekend, Mon
        self.assertEqual(apply_due_days(d("2026-11-10"), 0), d("2026-11-12"))
        self.assertEqual(roll_to_working_day(d("2026-11-10")), d("2026-11-12"))

    # 4
    def test_a_weekend_followed_by_a_holiday_moves_to_the_first_working_day(self):
        set_configured_holidays({d("2026-10-19"): "Monday holiday"})
        self.assertEqual(self.nwd("2026-10-17"), "2026-10-20")  # Sat, Sun, holiday Mon -> Tue
        self.assertEqual(self.nwd("2026-10-18"), "2026-10-20")  # Sun, holiday Mon -> Tue

    def test_a_sunday_holiday_followed_by_a_monday_holiday_keeps_advancing(self):
        set_configured_holidays({d("2026-10-18"): "Sunday holiday", d("2026-10-19"): "Monday holiday"})
        self.assertEqual(self.nwd("2026-10-18"), "2026-10-20")

    # 5
    def test_consecutive_holidays_and_weekends_are_all_skipped(self):
        # Dashain: Sat 17, Sun 18 (both holidays), Mon 19 / Tue 20 working.
        self.assertEqual(self.nwd("2026-10-17"), "2026-10-19")
        # Dashami + Ekadashi: Wed 21, Thu 22 -> Fri 23
        self.assertEqual(self.nwd("2026-10-21"), "2026-10-23")
        # Tihar: Sun 8 (holiday + weekend) -> Mon 9; Tue 10, Wed 11 -> Thu 12
        self.assertEqual(self.nwd("2026-11-08"), "2026-11-09")
        self.assertEqual(self.nwd("2026-11-10"), "2026-11-12")
        self.assertEqual(self.nwd("2026-11-11"), "2026-11-12")

    def test_a_long_run_of_holidays_is_walked_to_its_end(self):
        run = {d("2026-11-02") + timedelta(days=i): f"day {i}" for i in range(20)}
        set_configured_holidays(run)
        self.assertEqual(self.nwd("2026-11-02"), "2026-11-23")  # day 20 is a Monday

    def test_the_date_never_moves_backward_when_rolling_forward(self):
        for text in ("2026-10-17", "2026-11-08", "2026-11-10", "2027-01-01", "2027-03-06", "2027-03-21"):
            self.assertGreaterEqual(next_working_day(d(text)), d(text), text)

    def test_previous_working_day_steps_back_over_holidays_and_weekends(self):
        self.assertEqual(previous_working_day(d("2026-10-18")).isoformat(), "2026-10-16")  # Sun, Sat -> Fri
        self.assertEqual(previous_working_day(d("2026-10-22")).isoformat(), "2026-10-20")  # Thu, Wed -> Tue

    # 6
    def test_two_records_on_one_date_are_one_non_working_day(self):
        set_holiday_entries(_entries() + [HolidayEntry(d("2026-11-10"), "Another name for the same day")])
        self.assertEqual(self.nwd("2026-11-10"), "2026-11-12")  # not 2026-11-13
        self.assertEqual(
            working_days_between(d("2026-11-06"), d("2026-11-13")),
            working_days_between(d("2026-11-06"), d("2026-11-13")),
        )
        # Fri 6 -> Fri 13: Mon 9, Thu 12, Fri 13 are working; Tue 10 / Wed 11 are not
        self.assertEqual(working_days_between(d("2026-11-06"), d("2026-11-13")), 3)

    def test_multiple_names_on_a_date_are_all_kept_for_display_but_counted_once(self):
        set_holiday_entries([
            HolidayEntry(d("2026-11-10"), "A"), HolidayEntry(d("2026-11-10"), "B"),
            HolidayEntry(d("2026-11-10"), "A"),
        ])
        self.assertEqual(configured_holidays(), {d("2026-11-10"): "A / B"})

    def test_a_holiday_on_a_weekend_is_not_a_second_lost_working_day(self):
        # Tihar Sunday Nov 8 sits inside (Nov 6, Nov 9]: only Mon 9 counts.
        self.assertEqual(working_days_between(d("2026-11-06"), d("2026-11-09")), 1)

    # 8 / 9 live in StaffApplicabilityTests

    # Rule C: a calendar can't hang the request
    def test_an_endless_run_of_holidays_fails_loudly_instead_of_looping(self):
        everything = {d("2026-01-01") + timedelta(days=i): "x" for i in range(500)}
        set_configured_holidays(everything)
        with self.assertRaises(ValueError):
            next_working_day(d("2026-02-01"))
        with self.assertRaises(ValueError):
            previous_working_day(d("2027-05-01"))
        self.assertEqual(nepali_calendar._MAX_SCAN_DAYS, 366)

    def test_a_week_made_only_of_weekend_days_is_still_refused(self):
        with self.assertRaises(ValueError):
            next_working_day(d("2026-10-19"), weekend=range(7))


class EveryRecordedHolidayIsSkippedTests(ManualHolidayTestCase):
    """The owner's rule: every holiday recorded in the DB is skipped like the
    weekend — whatever its category."""

    def setUp(self):
        super().setUp()
        self.load_2083()

    SCOPED = ("2026-09-14", "2026-12-25", "2027-03-08")  # Teej, Christmas, Women's Day

    def test_all_thirteen_recorded_dates_are_non_working_days(self):
        self.assertEqual(len(configured_holidays()), 13)
        for _name, _bs, ad, *_ in migration.HOLIDAYS_2083:
            self.assertTrue(is_non_working_day(ad), ad)

    def test_gender_and_religion_holidays_are_skipped_for_everyone_too(self):
        for text in self.SCOPED:
            for who in (None, StaffProfile(), StaffProfile(gender="Male"), StaffProfile(religion="Hindu")):
                self.assertTrue(is_non_working_day(d(text), staff=who), (text, who))

    def test_due_dates_pickers_and_rolling_skip_them_like_a_weekend(self):
        for text in self.SCOPED:
            with self.assertRaises(NonWorkingDayError, msg=text):
                validate_working_day(d(text), label="Task")
            self.assertGreater(next_working_day(d(text)), d(text), text)
        self.assertEqual(next_working_day(d("2026-12-25")), d("2026-12-28"))  # Christmas Fri, weekend, Mon
        self.assertEqual(apply_due_days(d("2027-03-08"), 0), d("2027-03-09"))  # Women's Day Mon -> Tue
        self.assertEqual(next_working_day(d("2027-03-06")), d("2027-03-09"))  # Shivaratri Sat, Mon holiday -> Tue

    def test_daily_tasks_skip_christmas_and_womens_day(self):
        days = occurrence_dates(
            frequency=F.DAILY, interval_count=1, days_of_week=None, end=None,
            start=d("2026-12-23"), range_start=d("2026-12-23"), range_end=d("2026-12-30"),
        )
        self.assertEqual([x.isoformat() for x in days],
                         ["2026-12-23", "2026-12-24", "2026-12-28", "2026-12-29", "2026-12-30"])

    def test_a_date_with_several_holidays_shows_all_their_names_once(self):
        set_holiday_entries(_entries() + [HolidayEntry(d("2026-12-25"), "Christmas Day (observed)", "ALL")])
        self.assertEqual(configured_holidays()[d("2026-12-25")], "Christmas / Christmas Day (observed)")
        self.assertEqual(next_working_day(d("2026-12-25")), d("2026-12-28"))

    def test_the_api_says_every_recorded_holiday_is_non_working(self):
        for category in ("ALL", "GENDER", "RELIGION"):
            row = SimpleNamespace(
                id="x", holiday_date=d("2026-12-25"), bs_date=None, name="N", category=category,
                audience=None, remarks=None, source="MANUAL",
            )
            self.assertTrue(HolidayOut.model_validate(row).non_working, category)
        self.assertTrue(holiday_skips_everyone("ALL") and holiday_skips_everyone("GENDER") and holiday_skips_everyone("RELIGION"))
        self.assertFalse(holiday_skips_everyone("SOMETHING_ELSE"))


class StaffSpecificPolicyTests(ManualHolidayTestCase):
    """If the owner ever wants gender / religion holidays for matching staff
    only, removing them from SKIPPED_FOR_EVERYONE is enough — the staff-aware
    helpers then apply them per person and the global set shrinks to ALL."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(holiday_registry, "SKIPPED_FOR_EVERYONE", frozenset({"ALL"}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.load_2083()
        self.female = StaffProfile(gender="Female")
        self.male = StaffProfile(gender="Male")
        self.christian = StaffProfile(religion="Christian")
        self.hindu = StaffProfile(religion="Hindu")
        self.nobody = StaffProfile()

    def test_a_holiday_for_all_applies_to_everyone(self):
        for who in (self.female, self.male, self.christian, self.hindu, self.nobody, None):
            self.assertTrue(is_non_working_day(d("2026-11-10"), staff=who))
            self.assertTrue(is_holiday_for(d("2026-11-10"), who))

    def test_a_gender_holiday_affects_only_the_staff_it_is_for(self):
        teej = d("2026-09-14")  # a Monday
        self.assertTrue(is_non_working_day(teej, staff=self.female))
        self.assertFalse(is_non_working_day(teej, staff=self.male))
        self.assertFalse(is_non_working_day(teej, staff=self.nobody))
        self.assertFalse(is_non_working_day(teej))

    def test_the_gender_match_ignores_case_and_spaces(self):
        self.assertTrue(is_non_working_day(d("2026-09-14"), staff=StaffProfile(gender=" female ")))

    def test_an_unknown_gender_is_never_assumed(self):
        self.assertFalse(is_non_working_day(d("2027-03-08"), staff=StaffProfile(gender=None)))
        self.assertFalse(is_non_working_day(d("2027-03-08"), staff=StaffProfile(religion="Christian")))

    def test_a_religion_holiday_affects_only_the_staff_it_is_for(self):
        christmas = d("2026-12-25")  # a Friday
        self.assertTrue(is_non_working_day(christmas, staff=self.christian))
        self.assertFalse(is_non_working_day(christmas, staff=self.hindu))
        self.assertFalse(is_non_working_day(christmas, staff=self.female))
        self.assertFalse(is_non_working_day(christmas))

    def test_a_scoped_holiday_then_never_moves_the_global_task_dates(self):
        for text in ("2026-09-14", "2026-12-25", "2027-03-08"):
            self.assertFalse(is_non_working_day(d(text)), text)
            self.assertEqual(next_working_day(d(text)), d(text), text)
            validate_working_day(d(text), label="Task")  # must not raise
            self.assertEqual(apply_due_days(d(text), 0), d(text), text)

    def test_the_next_working_day_follows_the_persons_own_holidays(self):
        sat = d("2027-03-06")  # Maha Shivaratri, a Saturday
        self.assertEqual(next_working_day(sat, staff=self.male), d("2027-03-08"))
        self.assertEqual(next_working_day(sat, staff=self.female), d("2027-03-09"))  # Women's Day Mon
        self.assertEqual(next_working_day(sat), d("2027-03-08"))
        self.assertEqual(previous_working_day(d("2027-03-08"), staff=self.female), d("2027-03-05"))

    def test_the_scoped_entries_are_kept_for_lookups(self):
        self.assertEqual([e.name for e in scoped_holidays_on(d("2026-12-25"))], ["Christmas"])
        self.assertEqual(scoped_holidays_on(d("2026-11-10")), ())

    def test_an_audience_less_scoped_holiday_matches_nobody(self):
        set_holiday_entries([HolidayEntry(d("2026-12-01"), "Mystery", "GENDER", None)])
        self.assertFalse(is_holiday_for(d("2026-12-01"), self.female))

    def test_the_global_set_is_then_only_the_ALL_holidays(self):
        ids = sorted(configured_holidays())
        self.assertEqual(len(ids), 10)
        for text in ("2026-09-14", "2026-12-25", "2027-03-08"):
            self.assertNotIn(d(text), ids)

    def test_the_api_flag_follows_the_same_decision(self):
        self.assertFalse(holiday_skips_everyone("GENDER"))
        self.assertTrue(holiday_skips_everyone("ALL"))


class OnlyRecordedHolidaysAreMarkedTests(ManualHolidayTestCase):
    """A day is a holiday only if it is recorded in the DB. The `holidays`
    library's estimated festival dates must not be shown, counted or skipped."""

    # Library-only weekdays (not in the 2083 list): Maha Ashtami/Navami (Mon), Bijaya
    # Dashami (Tue), Gai Tihar (Mon), Prithvi Jayanti (Mon), Nepali New Year (Wed).
    LIBRARY_ONLY = ("2026-10-19", "2026-10-20", "2026-11-09", "2027-01-11", "2027-04-14")

    def setUp(self):
        super().setUp()
        self.load_2083()

    def test_the_library_really_does_list_those_dates(self):
        est = nepal_holidays([2026, 2027])
        for text in self.LIBRARY_ONLY:
            self.assertIn(d(text), est, text)

    def test_known_holidays_is_exactly_the_recorded_dates(self):
        shown = known_holidays([2026, 2027])
        self.assertEqual(set(shown), {h[2] for h in migration.HOLIDAYS_2083})
        for text in self.LIBRARY_ONLY:
            self.assertNotIn(d(text), shown, text)

    def test_known_holidays_follows_the_years_asked_for(self):
        self.assertEqual({x.year for x in known_holidays([2027])}, {2027})
        self.assertEqual(known_holidays([2030]), {})

    def test_with_nothing_recorded_nothing_is_marked(self):
        set_configured_holidays({})
        self.assertEqual(known_holidays([2026, 2027]), {})

    def test_a_recorded_name_is_shown_not_the_librarys(self):
        self.assertEqual(known_holidays([2026])[d("2026-11-10")], "Tihar-Gobardhan Puja")

    def test_a_library_festival_is_an_ordinary_working_day(self):
        for text in self.LIBRARY_ONLY:
            day = d(text)
            self.assertFalse(is_non_working_day(day), text)
            self.assertEqual(next_working_day(day), day, text)
            info = classify_day(day)
            self.assertIsNone(info.holiday, text)
            self.assertEqual(info.is_working_day, day.weekday() < 5, text)

    def test_a_recorded_holiday_is_marked_by_classify_day(self):
        info = classify_day(d("2026-11-10"))
        self.assertEqual((info.holiday, info.is_working_day), ("Tihar-Gobardhan Puja", False))

    def test_working_day_counts_ignore_library_dates(self):
        # Mon 19, Tue 20 (library festivals, not recorded) and Fri 23 are working; Wed 21 / Thu 22 are recorded.
        self.assertEqual(working_day_count(d("2026-10-19"), d("2026-10-23")), 3)

    def test_the_period_summary_lists_only_recorded_holidays(self):
        summary = Period(2083, 7).summary()  # Kartik 2083: 2026-10-18 .. 2026-11-16
        self.assertEqual(
            summary["holidays"],
            {"2026-10-18": "Dashain-Astami", "2026-10-21": "Dashain-Dashami", "2026-10-22": "Dashain-Ekadashi",
             "2026-11-08": "Tihar-Laxmi Puja", "2026-11-10": "Tihar-Gobardhan Puja", "2026-11-11": "Tihar-Bhaitika"},
        )
        start, end = d("2026-10-18"), d("2026-11-16")
        expected = sum(
            1 for i in range((end - start).days + 1)
            if not is_non_working_day(start + timedelta(days=i))
        )
        self.assertEqual(summary["working_days"], expected)
        self.assertEqual(summary["closing_date_ad"], "2026-11-16")

    def test_the_closing_date_skips_a_recorded_holiday_but_not_a_library_one(self):
        # Ashwin 2083 ends Sat 2026-10-17 (a recorded Dashain day on a weekend) -> Fri 16.
        self.assertEqual(last_working_day(2083, 6).ad.isoformat(), "2026-10-16")
        set_holiday_entries([HolidayEntry(d("2026-11-16"), "Closing-day holiday")])  # Kartik's last day
        self.assertEqual(last_working_day(2083, 7).ad.isoformat(), "2026-11-13")
        set_configured_holidays({})
        self.assertEqual(last_working_day(2083, 7).ad.isoformat(), "2026-11-16")

    def test_the_calendar_export_holds_only_recorded_holidays(self):
        events = fiscal_year_holiday_events(FiscalYear(2083))
        self.assertEqual({day for day, _ in events}, {h[2] for h in migration.HOLIDAYS_2083})
        self.assertEqual(len(events), 13)

    def test_the_bs_month_payload_marks_only_recorded_days(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.auth import require_tenant
        from app.routers import calendar as calendar_router

        app = FastAPI()
        app.include_router(calendar_router.router)
        app.dependency_overrides[require_tenant] = lambda: SimpleNamespace(id="u", tenant_id="t")
        client = TestClient(app)
        prefix = calendar_router.router.prefix
        marked = {}
        for bs_month in (6, 7, 8, 9):  # Ashwin .. Poush 2083 (mid Sep 2026 .. mid Jan 2027)
            body = client.get(f"{prefix}/bs-month", params={"bs_year": 2083, "bs_month": bs_month}).json()
            for day in body["days"]:
                if day["holiday"]:
                    marked[day["ad_date"]] = day["holiday"]
                    self.assertTrue(day["is_non_working"], day)  # a marked day is a skipped day
        recorded = {h[2].isoformat(): h[0] for h in migration.HOLIDAYS_2083 if date(2026, 10, 1) <= h[2] <= date(2027, 1, 13)}
        self.assertEqual(set(marked), set(recorded))
        for text in self.LIBRARY_ONLY[:4]:
            self.assertNotIn(text, marked)
        # ...and every non-weekend, non-recorded day is plain working
        body = client.get(f"{prefix}/bs-month", params={"bs_year": 2083, "bs_month": 7}).json()
        by_date = {x["ad_date"]: x for x in body["days"]}
        self.assertFalse(by_date["2026-11-09"]["is_non_working"])  # Gai Tihar per the library
        self.assertIsNone(by_date["2026-11-09"]["holiday"])


class ExistingWorkflowTests(ManualHolidayTestCase):
    """The 2083 list loaded: weekend rules, picks and generation still work."""

    def setUp(self):
        super().setUp()
        self.load_2083()

    def test_a_picked_due_date_on_a_holiday_or_weekend_is_refused_but_a_working_day_is_not(self):
        for text in ("2026-11-10", "2026-11-08", "2027-03-21", "2026-10-24"):
            with self.assertRaises(NonWorkingDayError, msg=text):
                validate_working_day(d(text), label="Task")
        validate_working_day(d("2026-11-12"), label="Task")
        validate_working_day(None, label="Task")

    def test_an_already_stored_holiday_date_can_be_resubmitted_unchanged(self):
        validate_working_day(d("2026-11-10"), current=d("2026-11-10"), label="Task")

    def test_the_error_names_the_holiday(self):
        with self.assertRaises(NonWorkingDayError) as ctx:
            validate_working_day(d("2026-11-10"), label="Task")
        self.assertIn("Tihar-Gobardhan Puja", str(ctx.exception))

    def test_daily_tasks_skip_weekends_and_the_tihar_holidays(self):
        days = occurrence_dates(
            frequency=F.DAILY, interval_count=1, days_of_week=None, end=None,
            start=d("2026-11-02"), range_start=d("2026-11-02"), range_end=d("2026-11-16"),
        )
        self.assertEqual(
            [x.isoformat() for x in days],
            ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
             "2026-11-09", "2026-11-12", "2026-11-13", "2026-11-16"],
        )

    def test_weekly_on_a_holiday_keeps_the_week_and_moves_only_the_due_date(self):
        days = occurrence_dates(
            frequency=F.WEEKLY, interval_count=1, days_of_week=[1], end=None,
            start=d("2026-11-09"), range_start=d("2026-11-09"), range_end=d("2026-11-15"),
        )
        self.assertEqual(days, [d("2026-11-10")])
        self.assertEqual(apply_due_days(days[0], 0), d("2026-11-12"))

    def test_due_dates_still_stay_inside_their_period(self):
        # Kartik 2083 ends Mon 2026-11-16, a working day; Tihar must not push anything past it.
        due = apply_due_days(d("2026-11-11"), 3, period_start=d("2026-10-18"), period_end=d("2026-11-16"))
        self.assertLessEqual(due, d("2026-11-16"))
        self.assertFalse(is_non_working_day(due))

    def test_removing_all_holidays_gives_back_the_pure_weekend_rule(self):
        set_configured_holidays({})
        self.assertEqual(next_working_day(d("2026-11-10")), d("2026-11-10"))
        self.assertEqual(next_working_day(d("2026-11-08")), d("2026-11-09"))


class PersistenceAndRerunTests(ManualHolidayTestCase, unittest.IsolatedAsyncioTestCase):
    """The registry is rebuilt from table rows; a re-run changes nothing."""

    def _rows(self):
        return [
            Holiday(holiday_date=ad, bs_date=bs, name=name, category=cat, audience=aud, remarks=rem, source="MANUAL")
            for name, bs, ad, cat, aud, rem in migration.HOLIDAYS_2083
        ]

    def _db(self, rows):
        result = mock.Mock()
        result.scalars.return_value.all.return_value = list(rows)
        added = []
        return SimpleNamespace(
            add=added.append, flush=mock.AsyncMock(), execute=mock.AsyncMock(return_value=result)
        ), added

    # 11
    async def test_a_restart_rebuilds_exactly_the_same_registry_from_the_stored_rows(self):
        db, _ = self._db(self._rows())
        await holiday_service.refresh_holiday_registry(db)
        before = (configured_holidays(), scoped_holidays_on(d("2026-12-25")))
        set_configured_holidays({})  # a fresh process: empty registry
        self.assertEqual(configured_holidays(), {})
        await holiday_service.refresh_holiday_registry(db)
        self.assertEqual((configured_holidays(), scoped_holidays_on(d("2026-12-25"))), before)
        self.assertEqual(len(before[0]), 13)  # every recorded holiday is skipped

    async def test_every_stored_row_becomes_a_non_working_day_whatever_its_category(self):
        db, _ = self._db(self._rows())
        await holiday_service.refresh_holiday_registry(db)
        for _name, _bs, ad, *_ in migration.HOLIDAYS_2083:
            self.assertTrue(is_non_working_day(ad), ad)
        self.assertTrue(is_non_working_day(d("2026-09-14")))  # Teej, gender-specific
        self.assertEqual(len(known_holidays([2026, 2027])), 13)

    # 12
    async def test_posting_the_whole_list_again_creates_and_changes_nothing(self):
        rows = self._rows()
        db, added = self._db(rows)
        drafts = [
            holiday_service.HolidayDraft(ad, name, cat, aud, rem)
            for name, _bs, ad, cat, aud, rem in migration.SUPPLIED_2083  # all 14, Christmas twice
        ]
        self.assertEqual(await holiday_service.upsert_holidays(db, drafts), (0, 0, 0))
        self.assertEqual(added, [])

    async def test_posting_the_list_into_an_empty_table_creates_thirteen(self):
        db, added = self._db([])
        drafts = [
            holiday_service.HolidayDraft(ad, name, cat, aud, rem)
            for name, _bs, ad, cat, aud, rem in migration.SUPPLIED_2083
        ]
        self.assertEqual(await holiday_service.upsert_holidays(db, drafts), (13, 0, 0))
        self.assertEqual(len(added), 13)
        self.assertEqual({h.bs_date for h in added if h.name == "Holi"}, {"2083-12-07"})


class HolidayRequestTests(unittest.TestCase):
    def test_a_global_holiday_needs_only_a_date_and_a_name(self):
        h = HolidayIn(holiday_date="2026-11-10", name="  Tihar  ")
        self.assertEqual((h.name, h.category, h.audience, h.bs_date, h.remarks), ("Tihar", "ALL", None, None, None))

    def test_the_category_is_accepted_in_any_case(self):
        for text in ("gender", "Gender", " GENDER "):
            self.assertEqual(HolidayIn(holiday_date="2026-09-14", name="Teej", category=text, audience="f").category, "GENDER")

    def test_a_scoped_holiday_must_say_who_it_is_for(self):
        for category in ("gender", "religion"):
            with self.assertRaises(ValidationError):
                HolidayIn(holiday_date="2026-09-14", name="X", category=category)
            with self.assertRaises(ValidationError):
                HolidayIn(holiday_date="2026-09-14", name="X", category=category, audience="   ")

    def test_an_audience_on_an_all_holiday_is_dropped(self):
        self.assertIsNone(HolidayIn(holiday_date="2026-11-10", name="X", audience="Female").audience)

    def test_an_unknown_category_is_refused(self):
        with self.assertRaises(ValidationError):
            HolidayIn(holiday_date="2026-11-10", name="X", category="everyone")

    def test_a_matching_bs_date_is_accepted_and_a_wrong_one_is_refused(self):
        self.assertEqual(HolidayIn(holiday_date="2026-11-10", name="X", bs_date="2083-07-24").bs_date, "2083-07-24")
        with self.assertRaises(ValidationError):
            HolidayIn(holiday_date="2026-11-10", name="X", bs_date="2083-07-25")

    def test_a_blank_name_is_refused(self):
        with self.assertRaises(ValidationError):
            HolidayIn(holiday_date="2026-11-10", name="   ")

    def test_an_implausible_year_is_refused(self):
        with self.assertRaises(ValidationError):
            HolidayIn(holiday_date="1999-01-01", name="X")


if __name__ == "__main__":
    unittest.main()
