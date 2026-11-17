"""The calendar CloseLoop runs on: Nepal time (UTC+05:45, no daylight saving).

The app's dashboards (backend `calendar.nepal_today`) and its screens decide "due today" / "overdue" by
Nepal's date, while the server and the database run on UTC. Between 00:00 and 05:45 in Nepal the two dates
differ, so the gateway answers with Nepal's date everywhere it says "today" and runs its queries in this zone.
"""
from datetime import date, datetime, timedelta, timezone

NEPAL_UTC_OFFSET = timedelta(hours=5, minutes=45)
NEPAL_TZ = timezone(NEPAL_UTC_OFFSET, "NPT")
# The name PostgreSQL knows the same zone by (its own tz database, so no OS tzdata is needed there).
NEPAL_PG_ZONE = "Asia/Kathmandu"


def nepal_today(now: datetime | None = None) -> date:
    """Today's date in Nepal. `now` (aware, any zone) is for tests; default is the current time."""
    return (now or datetime.now(timezone.utc)).astimezone(NEPAL_TZ).date()
