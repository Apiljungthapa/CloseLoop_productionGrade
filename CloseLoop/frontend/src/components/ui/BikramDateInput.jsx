// BikramDateInput — a Bikram Sambat (Nepali) date picker.
//
// The VALUE it reads and emits is always an AD ISO string ("YYYY-MM-DD") —
// the backend stores AD dates throughout; only the picking UI is Nepali.
// The user navigates BS month-by-month (via /api/calendar/bs-month) and the
// closed field shows the BS label (via /api/calendar/convert).
//
// Used through ../ui/DateInput, which falls back to a native <input
// type="date"> whenever the surrounding pack/plan is on the AD calendar.
import { useCallback, useEffect, useRef, useState } from 'react';
import { calendarAPI } from '../../apiEntities.js';

// The panel / grid / cell styles and DOW header below are also used by
// GregorianDateInput so the AD and BS pickers look identical.
export const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// Must outrank every other positioned element inside a modal/panel this
// opens in — in particular the sticky .detail-head / .detail-footer.
// (.modal-head / .modal-foot are no longer sticky — the modal body now
// scrolls in its own box, see the ".modal" comment in global.css /
// leaddash.css — but a positive z-index here is still cheap insurance.)
export const panelStyle = {
  position: 'absolute',
  zIndex: 200,
  marginTop: 4,
  background: '#fff',
  border: '1px solid var(--gray-line, #e2e5ec)',
  borderRadius: 10,
  boxShadow: '0 12px 32px rgba(20,30,60,.16)',
  padding: 10,
  width: 268,
};

export const gridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(7, 1fr)',
  gap: 2,
};

// `disabled` = outside the min/max window (faded). `nonWorkingBlocked` = a
// Saturday/Sunday or holiday that can't be picked: a flat grey cell with a
// not-allowed cursor, readable (not faded) so the closed day is obvious.
// A RECORDED holiday is always red — also when it can't be picked, and also
// when it falls on a weekend — and nothing else is: a weekend is grey.
// A selected day always keeps the selected colour so an existing value
// (e.g. a legacy weekend due date) stays visible.
export function cellStyle({ selected, today, disabled, weekend, holiday, nonWorkingBlocked }) {
  const blocked = nonWorkingBlocked && !selected;
  return {
    border: 'none',
    borderRadius: 6,
    padding: '6px 0',
    fontSize: 12.5,
    fontWeight: selected || today ? 800 : 600,
    cursor: disabled || nonWorkingBlocked ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.32 : 1,
    color: selected
      ? '#fff'
      : holiday
        ? 'var(--red, #d33)'
        : blocked || weekend ? 'var(--gray-mute, #9aa1ad)' : 'var(--ink, #26303f)',
    background: selected
      ? 'var(--blue, #2F55D4)'
      : blocked
        ? 'var(--gray-soft, #EEF0F4)'
        : today
          ? 'var(--blue-soft, #E7EDFF)'
          : 'transparent',
  };
}

export default function BikramDateInput({
  value = '',
  onChange,
  min,
  max,
  disabled = false,
  allowNonWorking = false,
  placeholder = 'Pick a Nepali date',
  id,
}) {
  const wrapRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState(null); // { bs_year, bs_month }
  const [month, setMonth] = useState(null); // BsMonthOut
  const [label, setLabel] = useState('');
  const [loading, setLoading] = useState(false);

  // Resolve the closed-field BS label + the initial month to show.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        if (value) {
          const { data } = await calendarAPI.convert(value);
          if (!alive) return;
          setLabel(data.bs_label);
          setView({ bs_year: data.bs_year, bs_month: data.bs_month });
        } else {
          setLabel('');
          if (!view) {
            const { data } = await calendarAPI.today();
            if (!alive) return;
            setView({ bs_year: data.bs_year, bs_month: data.bs_month });
          }
        }
      } catch {
        /* leave label blank on failure */
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const loadMonth = useCallback(async (v) => {
    if (!v) return;
    setLoading(true);
    try {
      const { data } = await calendarAPI.bsMonth(v.bs_year, v.bs_month);
      setMonth(data);
    } catch {
      setMonth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && view) loadMonth(view);
  }, [open, view, loadMonth]);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const step = (delta) => {
    if (!month) return;
    setView(delta < 0
      ? { bs_year: month.prev_bs_year, bs_month: month.prev_bs_month }
      : { bs_year: month.next_bs_year, bs_month: month.next_bs_month });
  };

  const pick = (day) => {
    onChange?.(day.ad_date);
    setOpen(false);
  };

  const isDisabled = (iso) => (min && iso < min) || (max && iso > max);

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <button
        type="button"
        id={id}
        className="field"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        style={{ textAlign: 'left', cursor: disabled ? 'not-allowed' : 'pointer' }}
      >
        {label
          ? <span>{label} <span style={{ color: 'var(--gray-mute,#9aa1ad)' }}>· BS</span></span>
          : <span style={{ color: 'var(--gray-mute,#9aa1ad)' }}>{placeholder}</span>}
      </button>

      {open && (
        <div style={panelStyle}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <button type="button" className="btn" style={{ padding: '2px 8px' }} onClick={() => step(-1)} aria-label="Previous month">‹</button>
            <div style={{ textAlign: 'center', lineHeight: 1.2 }}>
              <div style={{ fontWeight: 800, fontSize: 13 }}>
                {month ? `${month.bs_month_name} ${month.bs_year}` : '…'}
              </div>
              {month && <div style={{ fontSize: 10.5, color: 'var(--gray-mute,#9aa1ad)' }}>{month.ad_range_label}</div>}
            </div>
            <button type="button" className="btn" style={{ padding: '2px 8px' }} onClick={() => step(1)} aria-label="Next month">›</button>
          </div>

          <div style={{ ...gridStyle, marginBottom: 2 }}>
            {DOW.map((d) => (
              <div key={d} style={{ textAlign: 'center', fontSize: 10.5, fontWeight: 700, color: 'var(--gray-mute,#9aa1ad)' }}>{d}</div>
            ))}
          </div>

          {loading && !month ? (
            <div style={{ textAlign: 'center', padding: 16, fontSize: 12, color: 'var(--gray-mute,#9aa1ad)' }}>Loading…</div>
          ) : month ? (
            <div style={gridStyle}>
              {Array.from({ length: month.leading_blanks }).map((_, i) => <div key={`b${i}`} />)}
              {month.days.map((day) => {
                const disabledDay = isDisabled(day.ad_date);
                // Saturday/Sunday and the recorded holidays (the backend's
                // `is_non_working`) can't be picked, unless this field opts
                // out. `day.holiday` is set only for a recorded holiday, which
                // is drawn red; a weekend is grey.
                const nonWorking = day.is_non_working ?? day.is_weekend;
                const nonWorkingBlocked = !allowNonWorking && nonWorking;
                const selected = value === day.ad_date;
                return (
                  <button
                    key={day.ad_date}
                    type="button"
                    disabled={disabledDay || nonWorkingBlocked}
                    title={nonWorkingBlocked
                      ? (day.holiday ? `${day.holiday} (holiday) — not available` : 'Weekend — not available')
                      : (day.holiday || undefined)}
                    onClick={() => !disabledDay && !nonWorkingBlocked && pick(day)}
                    style={cellStyle({
                      selected,
                      today: day.is_today && !selected,
                      disabled: disabledDay,
                      weekend: day.is_weekend,
                      holiday: !!day.holiday,
                      nonWorkingBlocked,
                    })}
                  >
                    {day.bs_day}
                  </button>
                );
              })}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: 12, fontSize: 12, color: 'var(--red,#d33)' }}>Could not load the calendar.</div>
          )}

          {!allowNonWorking && (
            <div style={{ marginTop: 8, fontSize: 10.5, color: 'var(--gray-mute,#9aa1ad)' }}>
              Saturday, Sunday and holidays are not selectable.
              {month && month.days.some((d) => d.holiday) && (
                <div style={{ color: 'var(--red,#d33)' }}>
                  {month.days
                    .filter((d) => d.holiday)
                    .map((d) => `${d.bs_day} ${d.holiday}`)
                    .join(' · ')}
                </div>
              )}
            </div>
          )}

          {value && (
            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: 'var(--gray-mute,#9aa1ad)' }}>AD {value}</span>
              <button type="button" className="link-btn" onClick={() => { onChange?.(''); setOpen(false); }}>
                Clear
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
