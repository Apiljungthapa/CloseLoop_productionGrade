from datetime import date, datetime, timezone

from core.clock import nepal_today


def test_nepal_today_rolls_over_at_18_15_utc():
    assert nepal_today(datetime(2026, 10, 29, 18, 14, 59, tzinfo=timezone.utc)) == date(2026, 10, 29)
    assert nepal_today(datetime(2026, 10, 29, 18, 15, 0, tzinfo=timezone.utc)) == date(2026, 10, 30)


def test_nepal_today_matches_utc_date_in_the_rest_of_the_day():
    assert nepal_today(datetime(2026, 10, 30, 0, 0, 0, tzinfo=timezone.utc)) == date(2026, 10, 30)
    assert nepal_today(datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)) == date(2026, 10, 30)
