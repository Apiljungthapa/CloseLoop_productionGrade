// PeriodCalendarWidget — the "Period Calendar" panel on the Super Admin
// Periods page: a proper bilingual calendar. Navigation stays AD-month
// (periods are AD-dated), but every cell shows BOTH the English day number
// AND its accurate Bikram Sambat day number together — the way a real
// Nepali desk calendar pairs the two — plus Nepal public holidays and
// whichever tenant Period covers each day (colour-coded by current/open/
// closed/upcoming). Clicking a day shows its full AD + BS detail.
import { useCallback, useEffect, useState } from 'react';
import { Button, Spinner } from 'react-bootstrap';
import { calendarAPI } from '../apiEntities.js';
import { todayIso } from '../pages/leadDashUtils';

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function dayTone(day, currentPeriodId, today) {
  if (!day.period_id) return null;
  if (day.period_id === currentPeriodId) return 'current';
  if (day.period_end_date && day.period_end_date < today) return 'closed';
  if (day.period_start_date && day.period_start_date > today) return 'upcoming';
  return 'open';
}

function fmtAd(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

// An AD calendar month almost never lines up with a BS month — it spans
// the tail of one BS month and the head of the next — so the bilingual
// header shows that as a range, e.g. "Bhadra – Ashwin 2083", collapsing to
// one name only on the rare month that doesn't cross a BS boundary.
function bsHeaderLabel(days) {
  if (!days?.length) return '';
  const first = days[0];
  const last = days[days.length - 1];
  if (first.bs_year === last.bs_year && first.bs_month_name === last.bs_month_name) {
    return `${first.bs_month_name} ${first.bs_year}`;
  }
  const firstLabel = `${first.bs_month_name} ${first.bs_year}`;
  const lastLabel = last.bs_year === first.bs_year
    ? last.bs_month_name
    : `${last.bs_month_name} ${last.bs_year}`;
  return `${firstLabel} – ${lastLabel}`;
}

export default function PeriodCalendarWidget({ currentPeriod }) {
  const now = new Date();
  const [viewYear, setViewYear] = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [selected, setSelected] = useState(todayIso());

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const { data } = await calendarAPI.month(viewYear, viewMonth);
      setData(data);
    } catch (e) {
      setErr('Could not load the calendar.');
    } finally {
      setLoading(false);
    }
  }, [viewYear, viewMonth]);

  useEffect(() => { load(); }, [load]);

  function shiftMonth(delta) {
    let m = viewMonth + delta;
    let y = viewYear;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    setViewMonth(m);
    setViewYear(y);
  }

  function jumpToday() {
    const t = new Date();
    setViewYear(t.getFullYear());
    setViewMonth(t.getMonth() + 1);
    setSelected(todayIso());
  }

  const today = todayIso();
  const selectedDay = data?.days.find((d) => d.ad_date === selected) || null;

  return (
    <div className="period-calendar">
      <div className="period-calendar-head">
        <span className="pc-title"><i className="bi bi-calendar3 me-2"></i>Period Calendar</span>
        <div className="d-flex align-items-center gap-1">
          <Button size="sm" variant="light" onClick={() => shiftMonth(-1)} aria-label="Previous month">
            <i className="bi bi-chevron-left"></i>
          </Button>
          <Button size="sm" variant="light" onClick={jumpToday}>Today</Button>
          <Button size="sm" variant="light" onClick={() => shiftMonth(1)} aria-label="Next month">
            <i className="bi bi-chevron-right"></i>
          </Button>
        </div>
      </div>

      {loading && !data ? (
        <div className="text-center py-4"><Spinner animation="border" size="sm" /></div>
      ) : err ? (
        <div className="text-danger small py-2">{err}</div>
      ) : data ? (
        <>
          <div className="calendar-month-title">
            <div className="cm-ad">{data.ad_month_label}</div>
            <div className="cm-bs">BS {bsHeaderLabel(data.days)}</div>
          </div>

          <div className="calendar-grid">
            {DOW.map((d) => (
              <div key={d} className={`calendar-dow${d === 'Sat' ? ' is-weekend' : ''}`}>{d}</div>
            ))}
            {Array.from({ length: data.leading_blanks }).map((_, i) => (
              <div key={`blank-${i}`} />
            ))}
            {data.days.map((day) => {
              const tone = dayTone(day, currentPeriod?.id, today);
              const isToday = day.ad_date === today;
              const isSelected = day.ad_date === selected;
              const adDayNum = Number(day.ad_date.slice(-2));
              return (
                <button
                  type="button"
                  key={day.ad_date}
                  className={[
                    'calendar-day',
                    isToday && 'is-today',
                    isSelected && 'is-selected',
                    day.is_weekend && 'is-weekend',
                    day.holiday && 'has-holiday',
                  ].filter(Boolean).join(' ')}
                  title={[
                    day.holiday,
                    `BS ${day.bs_month_name} ${day.bs_day}, ${day.bs_year}`,
                    day.period_label,
                  ].filter(Boolean).join(' · ')}
                  onClick={() => setSelected(day.ad_date)}
                >
                  <span className="cd-ad">{adDayNum}</span>
                  <span className="cd-bs">{day.bs_day}</span>
                  {tone && <i className={`calendar-dot tone-${tone}`} />}
                </button>
              );
            })}
          </div>
          <div className="calendar-caption">
            <span className="cd-ad-swatch">EN</span> English date &nbsp; <span className="cd-bs-swatch">BS</span> Nepali date
          </div>

          <div className="calendar-legend">
            <span><i className="calendar-dot tone-current" />Current period</span>
            <span><i className="calendar-dot tone-open" />Open period</span>
            <span><i className="calendar-dot tone-closed" />Closed period</span>
            <span><i className="calendar-dot tone-upcoming" />Upcoming period</span>
            <span><i className="calendar-ring-swatch is-selected" />Selected date</span>
            <span><i className="calendar-ring-swatch is-today" />Today</span>
          </div>

          {selectedDay && (
            <div className="calendar-selected-info">
              <div className="text-muted small">Selected Date</div>
              <div className="fw-semibold">{fmtAd(selectedDay.ad_date)}</div>
              <div className="small" style={{ color: 'var(--cl-primary)' }}>
                BS {selectedDay.bs_month_name} {selectedDay.bs_day}, {selectedDay.bs_year}
              </div>
              {selectedDay.holiday && (
                <div className="small mt-1" style={{ color: 'var(--cl-danger)' }}>
                  <i className="bi bi-star-fill me-1"></i>{selectedDay.holiday}
                </div>
              )}
              {selectedDay.period_label ? (
                <div className="small mt-1">
                  Period: <strong>{selectedDay.period_label}</strong>
                  {selectedDay.period_id === currentPeriod?.id && (
                    <span className="text-primary"> (Current)</span>
                  )}
                  {' · '}{selectedDay.period_status}
                </div>
              ) : (
                <div className="small text-muted mt-1">No period covers this date.</div>
              )}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
