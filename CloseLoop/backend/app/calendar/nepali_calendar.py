"""Nepali (Bikram Sambat) calendar helpers for a period-closing application.

Wraps two independent, well-tested libraries:
  * nepali-datetime  -> AD <-> BS date conversion, BS month lengths
  * holidays          -> Nepal public-holiday list (lunar festivals are estimated)

Everything that involves weekday / working-day math is done on the AD
(``datetime.date``) value, because that is unambiguous. BS is used only for
labelling, month boundaries and fiscal periods.

Weekday conventions differ between libraries, so be careful:
  * datetime.date.weekday()      -> Mon=0 .. Sun=6   (used here for all logic)
  * nepali_datetime.date.weekday -> Sun=0 .. Sat=6   (only touched inside helpers)

CloseLoop treats Saturday AND Sunday as the weekend for every tenant (Nepal's
official week is Saturday-only, so this is deliberately wider than the
statutory one). That is the default here; pass ``weekend={5}`` to get the
Saturday-only behaviour back (datetime convention: Sat=5, Sun=6).

A day is a holiday only if it is RECORDED: the CONFIGURED holidays
(``holiday_registry``, loaded from the ``holidays`` table) are the ones an admin
has confirmed, and they are non-working days exactly like Saturday/Sunday — see
``is_non_working_day``. Everything that shows or counts holidays (the calendar
payloads, ``known_holidays``, ``working_days``, the period summary, the .ics
export) reads that same registry. The ``holidays`` library's Nepal table
(``nepal_holidays``) only ESTIMATES the lunar festivals; it is kept for
reference but nothing in the app uses it, so a festival that is not in the table
is never marked or skipped.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Container, Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

import nepali_datetime as _ndt

from .holiday_registry import (
    StaffProfile,
    configured_holiday_name,
    configured_holidays,
    count_configured_holidays_between,
    is_holiday_for,
)

try:  # optional, only needed for holiday-aware features
    import holidays as _holidays
except ImportError:  # pragma: no cover
    _holidays = None


# --- constants ---------------------------------------------------------------

# datetime.date.weekday() values that count as weekend (Saturday + Sunday).
# Single source of truth: task recurrence (services/recurrence.py), the
# calendar API's `is_weekend` flag, working-day counts and period closing
# dates all read this.
NEPAL_WEEKEND = frozenset({5, 6})

# BS month numbers -> English transliteration.
BS_MONTHS = {
    1: "Baisakh", 2: "Jestha", 3: "Ashadh", 4: "Shrawan",
    5: "Bhadra", 6: "Ashwin", 7: "Kartik", 8: "Mangsir",
    9: "Poush", 10: "Magh", 11: "Falgun", 12: "Chaitra",
}

# Same months, Devanagari script — used by the UI's "BS" display mode.
BS_MONTHS_NP = {
    1: "बैशाख", 2: "जेठ", 3: "असार", 4: "श्रावण",
    5: "भदौ", 6: "आश्विन", 7: "कार्तिक", 8: "मंसिर",
    9: "पुष", 10: "माघ", 11: "फाल्गुन", 12: "चैत्र",
}

_DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def to_devanagari_digits(value: int | str) -> str:
    """Render an integer (or numeric string) using Devanagari digits."""
    return str(value).translate(_DEVANAGARI_DIGITS)


# Nepal's fiscal year runs from Shrawan 1 (month 4) to the end of Ashadh.
FISCAL_YEAR_START_MONTH = 4

# Usable range. nepali-datetime's data is defined 1975-01-01..2100-12-30 BS,
# but AD->BS overflows a bit before the upper bound and holiday data is only
# reliable for roughly the current decade +/- a few years.
BS_MIN_YEAR = 1975
BS_MAX_YEAR = 2099


# --- core conversion -------------------------------------------------------


def to_bs(ad_date: _dt.date) -> _ndt.date:
    """Convert a Gregorian ``datetime.date`` to a Bikram Sambat date."""
    if isinstance(ad_date, _dt.datetime):
        ad_date = ad_date.date()
    return _ndt.date.from_datetime_date(ad_date)


def to_ad(bs_year: int, bs_month: int, bs_day: int) -> _dt.date:
    """Convert a Bikram Sambat (year, month, day) to a Gregorian date."""
    return _ndt.date(bs_year, bs_month, bs_day).to_datetime_date()


# Nepal is UTC+5:45 with no DST. Every "what day is it" decision that
# drives period boundaries, task overdue-ness or the auto-close scheduler
# must be pinned to this timezone rather than the server process's own
# (containers here run UTC) — otherwise the calendar date flips up to
# ~5h45m early relative to Nepal midnight, which showed up as periods
# (and the plans generated from them) rolling over during the last
# afternoon/evening of the outgoing period instead of at its actual end.
NEPAL_TZ = ZoneInfo("Asia/Kathmandu")


def nepal_today() -> _dt.date:
    """The current calendar date in Nepal, independent of server timezone.

    Single source of truth for "today" wherever period/task/report logic
    needs it — see module note on `NEPAL_TZ` for why this can't just be
    `datetime.date.today()`.
    """
    return _dt.datetime.now(NEPAL_TZ).date()


def due_label(due: Optional[_dt.date], today: Optional[_dt.date] = None) -> Optional[str]:
    """Relative due label: overdue / today / tomorrow / this week / next week,
    else "Due <date>". Weeks run Sunday-Saturday (Nepal). Mirrors the
    frontend's dueMeta.

    "Nd overdue" counts WORKING days (Saturday/Sunday and configured holidays
    don't age a task): due Friday, it reads "1d overdue" on Monday. Over a
    weekend no working day has passed yet, so it reads plain "Overdue"."""
    if due is None:
        return None
    today = today or nepal_today()
    days = (due - today).days
    if days < 0:
        overdue = working_days_between(due, today)
        return f"{overdue}d overdue" if overdue else "Overdue"
    if days == 0:
        return "Due today"
    if days == 1:
        return "Due tomorrow"
    to_saturday = (5 - today.weekday()) % 7
    if days <= to_saturday:
        return "Due this week"
    if days <= to_saturday + 7:
        return "Due next week"
    return f"Due {due.strftime('%b')} {due.day}, {due.year}"


def bs_today() -> _ndt.date:
    return _ndt.date.from_datetime_date(nepal_today())


def format_bs(bs: _ndt.date, style: str = "long") -> str:
    """Human-readable BS string, e.g. ``2083 Bhadra 22 (Monday)``."""
    month = BS_MONTHS[bs.month]
    if style == "iso":
        return f"{bs.year:04d}-{bs.month:02d}-{bs.day:02d}"
    if style == "short":
        return f"{bs.year} {month} {bs.day}"
    if style == "np":
        return f"{BS_MONTHS_NP[bs.month]} {to_devanagari_digits(bs.day)}, {to_devanagari_digits(bs.year)}"
    weekday = bs.strftime("%A")
    return f"{bs.year} {month} {bs.day} ({weekday})"


def bs_iso(ad_date: _dt.date) -> Optional[str]:
    """The Gregorian date as a Bikram Sambat "YYYY-MM-DD" string, or None when
    it falls outside the range the BS tables cover."""
    try:
        return format_bs(to_bs(ad_date), "iso")
    except (ValueError, OverflowError):
        return None


# --- BS month boundaries -------------------------------------------------------


def bs_month_length(bs_year: int, bs_month: int) -> int:
    """Number of days in the given BS month (29-32)."""
    try:
        return _ndt._days_in_month(bs_year, bs_month)
    except AttributeError:  # pragma: no cover - only if the private helper moves
        pass
    # Fallback: nepali_datetime.date() raises ValueError past the real last day.
    day = 29
    while day <= 32:
        try:
            _ndt.date(bs_year, bs_month, day + 1)
        except ValueError:
            return day
        day += 1
    return day


def bs_month_range(bs_year: int, bs_month: int) -> tuple[_ndt.date, _ndt.date]:
    """First and last BS date of the given BS month."""
    last = bs_month_length(bs_year, bs_month)
    return _ndt.date(bs_year, bs_month, 1), _ndt.date(bs_year, bs_month, last)


def bs_month_range_ad(bs_year: int, bs_month: int) -> tuple[_dt.date, _dt.date]:
    """First and last day of the given BS month, as Gregorian dates."""
    start, end = bs_month_range(bs_year, bs_month)
    return start.to_datetime_date(), end.to_datetime_date()


def bs_add_months(bs_year: int, bs_month: int, delta: int) -> tuple[int, int]:
    """(bs_year, bs_month) shifted by `delta` BS months (may be negative)."""
    zero_based = (bs_month - 1) + delta
    return bs_year + zero_based // 12, zero_based % 12 + 1


# --- fiscal year -----------------------------------------------------------


@dataclass(frozen=True)
class FiscalYear:
    """Nepal fiscal year, e.g. FY 2081/82 = Shrawan 1 2081 .. end of Ashadh 2082."""

    start_year: int  # BS year in which Shrawan 1 falls

    @property
    def end_year(self) -> int:
        return self.start_year + 1

    @property
    def label(self) -> str:
        return f"{self.start_year}/{str(self.end_year)[-2:]}"

    @property
    def bs_start(self) -> _ndt.date:
        return _ndt.date(self.start_year, FISCAL_YEAR_START_MONTH, 1)

    @property
    def bs_end(self) -> _ndt.date:
        last_month = FISCAL_YEAR_START_MONTH - 1 or 12
        end_year = self.end_year if FISCAL_YEAR_START_MONTH > 1 else self.start_year
        return _ndt.date(end_year, last_month, bs_month_length(end_year, last_month))

    @property
    def ad_start(self) -> _dt.date:
        return self.bs_start.to_datetime_date()

    @property
    def ad_end(self) -> _dt.date:
        return self.bs_end.to_datetime_date()

    def months(self) -> list[tuple[int, int]]:
        """The 12 (bs_year, bs_month) pairs of this fiscal year, in order."""
        out = []
        y, m = self.start_year, FISCAL_YEAR_START_MONTH
        for _ in range(12):
            out.append((y, m))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def quarters(self) -> list[list[tuple[int, int]]]:
        """The 4 quarters of this fiscal year, each 3 (bs_year, bs_month) pairs."""
        months = self.months()
        return [months[0:3], months[3:6], months[6:9], months[9:12]]

    def halves(self) -> list[list[tuple[int, int]]]:
        """The 2 halves of this fiscal year, each 6 (bs_year, bs_month) pairs."""
        months = self.months()
        return [months[0:6], months[6:12]]

    def contains(self, ad_date: _dt.date) -> bool:
        return self.ad_start <= _as_date(ad_date) <= self.ad_end


def fiscal_year_of(date_value) -> FiscalYear:
    """Return the FiscalYear containing the given AD or BS date."""
    bs = date_value if isinstance(date_value, _ndt.date) else to_bs(_as_date(date_value))
    if bs.month >= FISCAL_YEAR_START_MONTH:
        return FiscalYear(bs.year)
    return FiscalYear(bs.year - 1)


# --- holidays & working days -------------------------------------------------


def nepal_holidays(years: Iterable[int]) -> "dict[_dt.date, str]":
    """Nepal public holidays for the given Gregorian years, from the
    ``holidays`` library. NOT used by the app (see the module docstring): only
    recorded holidays count. Kept for reference and ad-hoc comparison.

    NOTE: festival dates that depend on the lunar calendar (Dashain, Tihar,
    Teej, Eid, ...) are *estimated* by the holidays library and should be
    reconciled against the official Nepal Gazette each year.
    """
    if _holidays is None:
        raise RuntimeError("pip install holidays")
    return dict(_holidays.Nepal(years=list(years)))


def known_holidays(years: Iterable[int]) -> "dict[_dt.date, str]":
    """Every holiday to SHOW for the given Gregorian years: the recorded
    (configured) ones and nothing else — no library estimates — so a calendar
    marks exactly the days the app skips. For calendars, previews and .ics
    exports; whether a day is a non-working day is `is_non_working_day`."""
    years = set(years)
    return {d: n for d, n in configured_holidays().items() if d.year in years}


def _as_date(value) -> _dt.date:
    if isinstance(value, _ndt.date):
        return value.to_datetime_date()
    if isinstance(value, _dt.datetime):
        return value.date()
    return value


def daterange(start, end) -> Iterator[_dt.date]:
    """Yield every Gregorian date from ``start`` to ``end`` inclusive."""
    start, end = _as_date(start), _as_date(end)
    day = start
    while day <= end:
        yield day
        day += _dt.timedelta(days=1)


@dataclass(frozen=True)
class DayInfo:
    ad: _dt.date
    bs: _ndt.date
    is_weekend: bool
    holiday: str | None

    @property
    def is_working_day(self) -> bool:
        return not self.is_weekend and self.holiday is None


def classify_day(
    date_value,
    *,
    weekend: Iterable[int] = NEPAL_WEEKEND,
    extra_holidays: "dict[_dt.date, str] | None" = None,
    holiday_map: "dict[_dt.date, str] | None" = None,
) -> DayInfo:
    """Return weekend / holiday / working-day status for a single date.

    A recorded (configured) holiday always counts. ``holiday_map`` and
    ``extra_holidays`` let a caller add days of its own; by default there is
    nothing else — in particular no library estimates."""
    ad = _as_date(date_value)
    weekend = set(weekend)
    name = configured_holiday_name(ad) or (holiday_map or {}).get(ad)
    if not name and extra_holidays:
        name = extra_holidays.get(ad)
    return DayInfo(ad=ad, bs=to_bs(ad), is_weekend=ad.weekday() in weekend, holiday=name)


def working_days(
    start,
    end,
    *,
    weekend: Iterable[int] = NEPAL_WEEKEND,
    extra_holidays: "dict[_dt.date, str] | None" = None,
) -> list[DayInfo]:
    """All working days in [start, end] inclusive (AD or BS inputs accepted)."""
    start, end = _as_date(start), _as_date(end)
    out = []
    for day in daterange(start, end):
        info = classify_day(day, weekend=weekend, extra_holidays=extra_holidays)
        if info.is_working_day:
            out.append(info)
    return out


def working_day_count(start, end, **kwargs) -> int:
    return len(working_days(start, end, **kwargs))


def last_working_day(
    bs_year: int,
    bs_month: int,
    *,
    weekend: Iterable[int] = NEPAL_WEEKEND,
    extra_holidays: "dict[_dt.date, str] | None" = None,
) -> DayInfo:
    """Last working day of a BS month - the natural period-closing date."""
    month_start, month_end = bs_month_range_ad(bs_year, bs_month)
    for day in reversed(list(daterange(month_start, month_end))):
        info = classify_day(day, weekend=weekend, extra_holidays=extra_holidays)
        if info.is_working_day:
            return info
    raise ValueError(f"no working day found in BS {bs_year}-{bs_month:02d}")


# Task due dates, calendars and overdue counts all follow the weekend
# (Saturday/Sunday) PLUS the CONFIGURED holidays (holiday_registry, loaded from
# the `holidays` table) — and deliberately not the library's `nepal_holidays()`:
# that table only estimates the lunar festivals, and a day silently marked or
# skipped without anyone recording it would be hard to explain. With no
# holidays configured these are the weekend-only helpers they always were.


def is_non_working_day(
    date_value,
    *,
    weekend: Container[int] = NEPAL_WEEKEND,
    staff: Optional[StaffProfile] = None,
) -> bool:
    """True for a weekend day or a configured holiday.

    Without ``staff`` that is the global rule every task date follows (weekend
    + holidays for everyone). Pass a `StaffProfile` to also count the
    gender / religion holidays that apply to that person."""
    day = _as_date(date_value)
    return day.weekday() in weekend or is_holiday_for(day, staff)


# A run of non-working days is a few days long (a weekend plus a festival). The
# registry is finite so the scan always ends, but it is capped anyway: a
# misconfigured calendar must fail loudly, never hang a request.
_MAX_SCAN_DAYS = 366


def _nearest_working_day(date_value, step: int, weekend, staff) -> _dt.date:
    day, weekend = _as_date(date_value), set(weekend)
    if len(weekend) >= 7:
        raise ValueError("every weekday is a weekend day")
    for _ in range(_MAX_SCAN_DAYS):
        if not is_non_working_day(day, weekend=weekend, staff=staff):
            return day
        day += _dt.timedelta(days=step)
    raise ValueError(
        f"no working day within {_MAX_SCAN_DAYS} days of {_as_date(date_value).isoformat()}"
    )


def next_working_day(
    date_value,
    *,
    weekend: Iterable[int] = NEPAL_WEEKEND,
    staff: Optional[StaffProfile] = None,
) -> _dt.date:
    """``date_value`` itself when it is a working day, else the first working
    day after it (Saturday/Sunday -> Monday, or later past a holiday)."""
    return _nearest_working_day(date_value, 1, weekend, staff)


def previous_working_day(
    date_value,
    *,
    weekend: Iterable[int] = NEPAL_WEEKEND,
    staff: Optional[StaffProfile] = None,
) -> _dt.date:
    """``date_value`` itself when it is a working day, else the last working
    day before it (Saturday/Sunday -> Friday, or earlier past a holiday)."""
    return _nearest_working_day(date_value, -1, weekend, staff)


def working_days_between(start, end, *, weekend: Iterable[int] = NEPAL_WEEKEND) -> int:
    """Working days in ``(start, end]`` — after ``start``, up to and
    including ``end``. 0 when ``end`` is not after ``start``. This is the
    "business days overdue" count: a task due Friday, checked on Monday, is 1
    working day overdue (Monday); checked on Saturday it is 0; and a holiday
    on that Monday makes it 0 again.

    Closed-form for the weekend (whole weeks + a short tail), then the
    configured holidays in the range come off — so a report over thousands of
    long-overdue rows doesn't loop day by day."""
    start, end, weekend = _as_date(start), _as_date(end), set(weekend)
    total = (end - start).days
    if total <= 0:
        return 0
    weeks, tail = divmod(total, 7)
    count = weeks * (7 - len(weekend))
    for i in range(1, tail + 1):
        if (start + _dt.timedelta(days=i)).weekday() not in weekend:
            count += 1
    return count - count_configured_holidays_between(start, end, exclude_weekdays=weekend)


# --- period-closing summary -------------------------------------------------


@dataclass(frozen=True)
class Period:
    """A BS month treated as an accounting period."""

    bs_year: int
    bs_month: int

    @property
    def name(self) -> str:
        return f"{BS_MONTHS[self.bs_month]} {self.bs_year}"

    @property
    def fiscal_year(self) -> FiscalYear:
        return fiscal_year_of(_ndt.date(self.bs_year, self.bs_month, 1))

    @property
    def ad_start(self) -> _dt.date:
        return bs_month_range_ad(self.bs_year, self.bs_month)[0]

    @property
    def ad_end(self) -> _dt.date:
        return bs_month_range_ad(self.bs_year, self.bs_month)[1]

    def summary(self, **kwargs) -> dict:
        wd = working_days(self.ad_start, self.ad_end, **kwargs)
        hol = known_holidays([self.ad_start.year, self.ad_end.year])
        holidays_in = {
            d: n for d, n in hol.items() if self.ad_start <= d <= self.ad_end
        }
        return {
            "period": self.name,
            "fiscal_year": self.fiscal_year.label,
            "bs_range": f"{self.bs_year}-{self.bs_month:02d}-01 .. "
            f"{self.bs_year}-{self.bs_month:02d}-{bs_month_length(self.bs_year, self.bs_month)}",
            "ad_range": f"{self.ad_start.isoformat()} .. {self.ad_end.isoformat()}",
            "total_days": (self.ad_end - self.ad_start).days + 1,
            "working_days": len(wd),
            "closing_date_ad": last_working_day(self.bs_year, self.bs_month, **kwargs).ad.isoformat(),
            "holidays": {d.isoformat(): n for d, n in sorted(holidays_in.items())},
        }


# --- text calendar --------------------------------------------------------


def month_calendar_text(bs_year: int, bs_month: int, *, mark_holidays: bool = True) -> str:
    """Return a BS month as a Sun-first text grid with holidays footnoted."""
    length = bs_month_length(bs_year, bs_month)
    first_bs = _ndt.date(bs_year, bs_month, 1)
    lead = first_bs.weekday()  # Sun=0 .. Sat=6

    hol_map = known_holidays([first_bs.to_datetime_date().year,
                              _ndt.date(bs_year, bs_month, length).to_datetime_date().year]) if mark_holidays else {}

    header = f"{BS_MONTHS[bs_month]} {bs_year}".center(28)
    lines = [header, " Su  Mo  Tu  We  Th  Fr  Sa"]
    row = ["   "] * lead
    notes = []
    for day in range(1, length + 1):
        ad = _ndt.date(bs_year, bs_month, day).to_datetime_date()
        marker = " "
        if ad.weekday() in NEPAL_WEEKEND:
            marker = "."
        if ad in hol_map:
            marker = "*"
            notes.append(f"  {day:>2} {BS_MONTHS[bs_month]}  {hol_map[ad]}")
        row.append(f"{day:>2}{marker}")
        if len(row) == 7:
            lines.append(" ".join(row))
            row = []
    if row:
        lines.append(" ".join(row))
    if notes:
        lines.append("")
        lines.append("  * holiday    . Saturday/Sunday")
        lines.extend(notes)
    return "\n".join(lines)


# --- iCalendar export -----------------------------------------------------


def export_ics(events: "Iterable[tuple[_dt.date, str]]", path: str, *, calendar_name: str = "Nepali Calendar") -> str:
    """Write (date, title) pairs to an .ics file. All-day events."""
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//nepali_calendar//period-closing//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)

    for day, title in events:
        day = _as_date(day)
        ev = Event()
        bs = to_bs(day)
        ev.add("summary", title)
        ev.add("dtstart", day)
        ev.add("dtend", day + _dt.timedelta(days=1))
        ev.add("description", f"BS {format_bs(bs, 'short')}")
        ev.add("uid", f"{day.isoformat()}-{abs(hash(title)) % 10**8}@nepali_calendar")
        cal.add_component(ev)

    data = cal.to_ical()
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def build_ics_bytes(events: "Iterable[tuple[_dt.date, str]]", *, calendar_name: str = "Nepali Calendar") -> bytes:
    """Same as `export_ics` but returns the .ics bytes directly (for an HTTP
    download response) instead of writing to a local file."""
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//nepali_calendar//period-closing//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)

    for day, title in events:
        day = _as_date(day)
        ev = Event()
        bs = to_bs(day)
        ev.add("summary", title)
        ev.add("dtstart", day)
        ev.add("dtend", day + _dt.timedelta(days=1))
        ev.add("description", f"BS {format_bs(bs, 'short')}")
        ev.add("uid", f"{day.isoformat()}-{abs(hash(title)) % 10**8}@nepali_calendar")
        cal.add_component(ev)

    return cal.to_ical()


def fiscal_year_holiday_events(fy: FiscalYear) -> list[tuple[_dt.date, str]]:
    """Every holiday inside a fiscal year, ready for export_ics."""
    hol = known_holidays([fy.ad_start.year, fy.ad_end.year])
    return sorted((d, n) for d, n in hol.items() if fy.ad_start <= d <= fy.ad_end)
