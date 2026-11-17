// GregorianDateInput — an English (AD) calendar date picker.
//
// Replaces the native <input type="date"> for AD packs/plans: a native date
// field can't grey out or disable particular days, and Saturday/Sunday and
// the configured holidays must not be selectable for task work. Looks and behaves like
// BikramDateInput (same panel, grid and cell styles), but the month grid is
// computed locally — no API call.
//
// The VALUE it reads and emits is an AD ISO string ("YYYY-MM-DD"), exactly
// like the native input it replaces. Used through ../ui/DateInput.
import { useEffect, useRef, useState } from 'react';
import { DOW, cellStyle, gridStyle, panelStyle } from './BikramDateInput';
import { holidayNameIso, holidaysInMonth, isWeekendIso, nonWorkingLabelIso } from './workingDays';

const pad = (n) => String(n).padStart(2, '0');
// `month` is 0-based, like Date.
const isoOf = (year, month, day) => `${year}-${pad(month + 1)}-${pad(day)}`;

// Today in Asia/Kathmandu — the same "today" the backend uses (nepal_today),
// so the highlighted day can't differ from what the server considers today.
function nepalTodayIso() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kathmandu', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function viewOf(iso) {
  const m = /^(\d{4})-(\d{2})-/.exec(String(iso || ''));
  return m ? { year: Number(m[1]), month: Number(m[2]) - 1 } : null;
}

function fieldLabel(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
  if (!m) return '';
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
    .toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

export default function GregorianDateInput({
  value = '',
  onChange,
  min,
  max,
  disabled = false,
  allowNonWorking = false,
  placeholder = 'Pick a date',
  id,
  className = 'field',
}) {
  const wrapRef = useRef(null);
  const todayIso = nepalTodayIso();
  // Month first shown when there is no value: today, unless the allowed
  // window starts later (e.g. an upcoming period) — then its first month.
  const fallbackView = () => viewOf(min && min > todayIso ? min : todayIso);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState(() => viewOf(value) || fallbackView());

  // Re-centre on the value whenever it changes or the panel is (re)opened.
  useEffect(() => {
    if (open) setView(viewOf(value) || fallbackView());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, value]);

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

  const step = (delta) => setView((v) => {
    const total = v.year * 12 + v.month + delta;
    return { year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 };
  });

  const { year, month } = view;
  const leadingBlanks = new Date(Date.UTC(year, month, 1)).getUTCDay(); // Sun-first
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const monthName = new Date(Date.UTC(year, month, 1))
    .toLocaleDateString('en-US', { month: 'long', timeZone: 'UTC' });

  const isOutOfRange = (iso) => (min && iso < min) || (max && iso > max);
  const label = fieldLabel(value);

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <button
        type="button"
        id={id}
        className={className}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        style={{ textAlign: 'left', cursor: disabled ? 'not-allowed' : 'pointer' }}
      >
        {label
          ? <span>{label}</span>
          : <span style={{ color: 'var(--gray-mute,#9aa1ad)' }}>{placeholder}</span>}
      </button>

      {open && (
        <div style={panelStyle}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <button type="button" className="btn" style={{ padding: '2px 8px' }} onClick={() => step(-1)} aria-label="Previous month">‹</button>
            <div style={{ fontWeight: 800, fontSize: 13 }}>{monthName} {year}</div>
            <button type="button" className="btn" style={{ padding: '2px 8px' }} onClick={() => step(1)} aria-label="Next month">›</button>
          </div>

          <div style={{ ...gridStyle, marginBottom: 2 }}>
            {DOW.map((d) => (
              <div key={d} style={{ textAlign: 'center', fontSize: 10.5, fontWeight: 700, color: 'var(--gray-mute,#9aa1ad)' }}>{d}</div>
            ))}
          </div>

          <div style={gridStyle}>
            {Array.from({ length: leadingBlanks }).map((_, i) => <div key={`b${i}`} />)}
            {Array.from({ length: daysInMonth }).map((_, i) => {
              const iso = isoOf(year, month, i + 1);
              const outOfRange = isOutOfRange(iso);
              const weekend = isWeekendIso(iso);
              const holiday = holidayNameIso(iso);
              const nonWorkingBlocked = !allowNonWorking && (weekend || !!holiday);
              const selected = value === iso;
              return (
                <button
                  key={iso}
                  type="button"
                  disabled={outOfRange || nonWorkingBlocked}
                  title={nonWorkingBlocked ? nonWorkingLabelIso(iso) : (holiday || undefined)}
                  onClick={() => {
                    if (outOfRange || nonWorkingBlocked) return;
                    onChange?.(iso);
                    setOpen(false);
                  }}
                  style={cellStyle({
                    selected,
                    today: iso === todayIso && !selected,
                    disabled: outOfRange,
                    weekend,
                    holiday: !!holiday,
                    nonWorkingBlocked,
                  })}
                >
                  {i + 1}
                </button>
              );
            })}
          </div>

          {!allowNonWorking && (
            <div style={{ marginTop: 8, fontSize: 10.5, color: 'var(--gray-mute,#9aa1ad)' }}>
              Saturday, Sunday and holidays are not selectable.
              {holidaysInMonth(year, month).length > 0 && (
                <div style={{ color: 'var(--red,#d33)' }}>
                  {holidaysInMonth(year, month)
                    .map(([iso, name]) => `${Number(iso.slice(8))} ${name}`)
                    .join(' · ')}
                </div>
              )}
            </div>
          )}

          {value && (
            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
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
