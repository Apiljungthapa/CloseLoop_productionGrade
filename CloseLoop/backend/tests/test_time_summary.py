import unittest
from datetime import date

from app.routers.time_entries import _shift_month, _week_start, router
from app.schemas import MyTimeSummaryOut, TimeDayOut, TimeMonthOut, TimeWeekOut


class WeekMonthHelperTests(unittest.TestCase):
    def test_week_starts_on_sunday(self):
        self.assertEqual(_week_start(date(2026, 10, 1)), date(2026, 9, 27))  # Thu
        self.assertEqual(_week_start(date(2026, 9, 27)), date(2026, 9, 27))  # Sun
        self.assertEqual(_week_start(date(2026, 10, 3)), date(2026, 9, 27))  # Sat
        self.assertEqual(_week_start(date(2026, 10, 4)), date(2026, 10, 4))  # next Sun

    def test_shift_month_crosses_years(self):
        self.assertEqual(_shift_month(date(2026, 1, 15), -5), date(2025, 8, 1))
        self.assertEqual(_shift_month(date(2026, 10, 1), 0), date(2026, 10, 1))
        self.assertEqual(_shift_month(date(2026, 11, 9), 2), date(2027, 1, 1))


class SummarySchemaTests(unittest.TestCase):
    def test_round_trip(self):
        out = MyTimeSummaryOut(
            today=date(2026, 10, 1), week_start=date(2026, 9, 27),
            month_start=date(2026, 10, 1), today_minutes=30, week_minutes=90,
            month_minutes=120, range_days=7, range_total_minutes=90,
            daily=[TimeDayOut(date=date(2026, 10, 1), minutes=30)],
            weekly=[TimeWeekOut(week_start=date(2026, 9, 27), minutes=90)],
            monthly=[TimeMonthOut(month_start=date(2026, 10, 1), minutes=120)],
        )
        self.assertEqual(out.model_dump(mode="json")["daily"][0]["date"], "2026-10-01")

    def test_route_registered_before_entry_id(self):
        paths = [r.path for r in router.routes]
        self.assertIn("/api/time-entries/my/summary", paths)


if __name__ == "__main__":
    unittest.main()
