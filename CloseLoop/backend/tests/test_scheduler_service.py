import unittest
from datetime import date, datetime, timezone

from app.services.scheduler_service import _get_closure_reference_date


class SchedulerServiceTests(unittest.TestCase):
    def test_get_closure_reference_date_converts_to_nepal_local_date(self):
        # 23:00 UTC on the 13th is 04:45 Nepal time on the 14th — the
        # reference date must be the Nepal-local day, not the UTC one.
        now = datetime(2026, 7, 13, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(_get_closure_reference_date(now), date(2026, 7, 14))

    def test_get_closure_reference_date_stays_on_same_nepal_day_near_utc_midnight(self):
        # 18:00 UTC on the 13th is still only 23:45 Nepal time on the 13th.
        now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(_get_closure_reference_date(now), date(2026, 7, 13))


if __name__ == "__main__":
    unittest.main()
