// "Time Log" — the signed-in user's own history of time spent (Team Lead and
// Team Member). Backed by GET /api/time-entries/my/summary: a daily bar
// chart over a selectable range, today / this week / this month totals, the
// last 8 weeks and the last 6 months.
//
// Under /users/:userId/time-log the SAME page shows one team member's history
// to their Team Lead (GET /api/time-entries/user/{id}/summary, same shape).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { timeEntryAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';

const RANGES = [7, 30, 90];

// 0 -> "0m", 45 -> "45m", 120 -> "2h", 150 -> "2h 30m"
function fmtMins(m) {
  const n = Math.max(0, Math.round(Number(m) || 0));
  const h = Math.floor(n / 60);
  const mm = n % 60;
  if (h === 0) return `${mm}m`;
  return mm === 0 ? `${h}h` : `${h}h ${String(mm).padStart(2, '0')}m`;
}

// 'YYYY-MM-DD' -> local Date (never `new Date(str)`, which parses as UTC and
// can land on the previous day west of Greenwich).
function parseDay(s) {
  const [y, m, d] = String(s).split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}
const fmtShort = (s) => parseDay(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
const fmtLong = (s) => parseDay(s).toLocaleDateString('en-US', {
  weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
});
function weekLabel(startStr) {
  const a = parseDay(startStr);
  const b = new Date(a.getFullYear(), a.getMonth(), a.getDate() + 6);
  const f = (d) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return `${f(a)} – ${f(b)}`;
}
const monthLabel = (startStr) => parseDay(startStr).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

// Round the y-axis to whole, readable hours: pick the smallest "nice" step
// that fits the tallest bar in <= 4 intervals. Always returns a positive top
// so an all-zero range still draws an axis.
const HOUR_STEPS = [0.25, 0.5, 1, 2, 4, 5, 10, 20, 50];
function niceScale(maxMinutes) {
  const maxH = Math.max(maxMinutes / 60, 0.25);
  const step = HOUR_STEPS.find((s) => Math.ceil(maxH / s) <= 4) || 50;
  const count = Math.ceil(maxH / step);
  const ticks = Array.from({ length: count + 1 }, (_, i) => Math.round(i * step * 60));
  return { top: count * step * 60, ticks };
}
const fmtTick = (m) => (m === 0 ? '0' : `${Math.round((m / 60) * 100) / 100}h`);

function DailyChart({ daily, today }) {
  const [hover, setHover] = useState(null);
  const n = daily.length;
  const max = daily.reduce((a, d) => Math.max(a, d.minutes), 0);
  const { top, ticks } = useMemo(() => niceScale(max), [max]);
  const labelEvery = n <= 7 ? 1 : n <= 30 ? 5 : 15;
  const h = hover != null ? daily[hover] : null;
  const pct = hover != null ? ((hover + 0.5) / n) * 100 : 0;
  const shift = pct < 15 ? '-12%' : pct > 85 ? '-88%' : '-50%';

  return (
    <div className="tl-chart">
      <div className="tl-y" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t} style={{ bottom: `${(t / top) * 100}%` }}>{fmtTick(t)}</span>
        ))}
      </div>
      <div className="tl-plot-wrap">
        <div className="tl-plot" onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <div key={t} className="tl-grid" style={{ bottom: `${(t / top) * 100}%` }} />
          ))}
          <div className="tl-cols">
            {daily.map((d, i) => (
              <div
                key={d.date}
                className={`tl-col${hover === i ? ' hover' : ''}`}
                onMouseEnter={() => setHover(i)}
              >
                <div
                  className={`tl-bar${d.date === today ? ' today' : ''}`}
                  style={{ height: `${(d.minutes / top) * 100}%`, minHeight: d.minutes > 0 ? 2 : 0 }}
                />
              </div>
            ))}
          </div>
          {h && (
            <div className="tl-tip" style={{ left: `${pct}%`, transform: `translateX(${shift})` }}>
              <div className="tl-tip-date">{fmtLong(h.date)}</div>
              <div className="tl-tip-val">{h.minutes > 0 ? fmtMins(h.minutes) : 'Nothing logged'}</div>
            </div>
          )}
        </div>
        <div className="tl-x" aria-hidden="true">
          {daily.map((d, i) => (
            <div key={d.date} className="tl-xcell">
              {(n - 1 - i) % labelEvery === 0 ? <span>{fmtShort(d.date)}</span> : null}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BarRows({ rows, current }) {
  const max = Math.max(1, ...rows.map((r) => r.minutes));
  return (
    <div className="tl-rows">
      {rows.map((r) => (
        <div className={`tl-row${r.key === current ? ' current' : ''}`} key={r.key}>
          <div className="tl-row-label">
            {r.label}
            {r.key === current && <span className="tl-now">{r.nowLabel}</span>}
          </div>
          <div className="tl-row-track">
            <div className="tl-row-fill" style={{ width: `${(r.minutes / max) * 100}%` }} />
          </div>
          <div className="tl-row-val">{fmtMins(r.minutes)}</div>
        </div>
      ))}
    </div>
  );
}

export default function TimeLogPage() {
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const { userId } = useParams(); // set only on a Team Lead viewing a member
  const [days, setDays] = useState(30);
  const [view, setView] = useState('chart'); // 'chart' | 'table'
  const [raw, setRaw] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const r = userId
        ? await timeEntryAPI.userSummary(userId, days)
        : await timeEntryAPI.mySummary(days);
      setRaw(r.data);
      setErr('');
    } catch (e) {
      if (userId && e?.response?.status === 403) {
        setErr('You can only view time logs for members of your team.');
      } else {
        setErr(getErrorMessage(e, userId ? 'Could not load this time log.' : 'Could not load your time log.'));
      }
    }
  }, [days, userId]);

  // Switching to another person must not flash the previous person's numbers.
  useEffect(() => { setRaw(null); setErr(''); }, [userId]);
  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  // A response that lands after switching person (or from own <-> member view)
  // belongs to someone else — never render it.
  const data = raw && (userId ? raw.user?.id === userId : !raw.user) ? raw : null;

  const weekRows = useMemo(() => (data?.weekly || []).slice().reverse().map((w) => ({
    key: w.week_start, label: weekLabel(w.week_start), minutes: w.minutes, nowLabel: 'This week',
  })), [data]);
  const monthRows = useMemo(() => (data?.monthly || []).slice().reverse().map((m) => ({
    key: m.month_start, label: monthLabel(m.month_start), minutes: m.minutes, nowLabel: 'This month',
  })), [data]);

  // Range switches refetch; `data` keeps the previous range until the new
  // one lands, so the page doesn't flash empty. Guard against a stale
  // response by only trusting data that matches the selected range.
  const ready = data && data.range_days === days;
  const who = userId ? (data?.user?.name || 'Team member') : '';
  const avg = ready ? Math.round(data.range_total_minutes / data.range_days) : 0;

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="back-link" onClick={() => navigate(orgPath(userId ? '/users' : '/'))}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} /> {userId ? 'Users' : 'Overview'}
        </div>

        <div className="page-head">
          <div>
            <h1>{userId ? `${who} — Time Log` : 'Time Log'}</h1>
            <p>
              {userId
                ? `${who}'s history of time spent, by day, week and month.`
                : 'Your own history of time spent, by day, week and month.'}
            </p>
          </div>
        </div>

        {err && <div className="reject-banner">{err}</div>}

        {!data ? (
          !err && <div className="empty-note">Loading…</div>
        ) : (
          <>
            <div className="tl-stats">
              <div className="card tl-stat tl-stat-hero">
                <div className="tl-stat-lbl">Today</div>
                <div className="tl-stat-num">{fmtMins(data.today_minutes)}</div>
                <div className="tl-stat-sub">{fmtShort(data.today)}</div>
              </div>
              <div className="card tl-stat">
                <div className="tl-stat-lbl">This week</div>
                <div className="tl-stat-num">{fmtMins(data.week_minutes)}</div>
                <div className="tl-stat-sub">Since {fmtShort(data.week_start)}</div>
              </div>
              <div className="card tl-stat">
                <div className="tl-stat-lbl">This month</div>
                <div className="tl-stat-num">{fmtMins(data.month_minutes)}</div>
                <div className="tl-stat-sub">
                  {parseDay(data.month_start).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
                </div>
              </div>
              <div className="card tl-stat">
                <div className="tl-stat-lbl">Daily average</div>
                <div className="tl-stat-num">{ready ? fmtMins(avg) : '—'}</div>
                <div className="tl-stat-sub">Last {days} days</div>
              </div>
            </div>

            <div className="panel-card tl-panel">
              <div className="tl-panel-head">
                <div>
                  <h3>Daily time spent</h3>
                  <div className="tl-sub">
                    {ready ? `${fmtMins(data.range_total_minutes)} over the last ${days} days` : 'Loading…'}
                  </div>
                </div>
                <div className="tl-controls">
                  <div className="tl-seg" role="group" aria-label="Range">
                    {RANGES.map((r) => (
                      <button
                        key={r} type="button"
                        className={days === r ? 'on' : ''}
                        aria-pressed={days === r}
                        onClick={() => setDays(r)}
                      >
                        {r}d
                      </button>
                    ))}
                  </div>
                  <div className="tl-seg" role="group" aria-label="View">
                    {['chart', 'table'].map((v) => (
                      <button
                        key={v} type="button"
                        className={view === v ? 'on' : ''}
                        aria-pressed={view === v}
                        onClick={() => setView(v)}
                      >
                        {v === 'chart' ? 'Chart' : 'Table'}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {!ready ? (
                <div className="empty-note">Loading…</div>
              ) : view === 'chart' ? (
                <>
                  <DailyChart daily={data.daily} today={data.today} />
                  {data.range_total_minutes === 0 && (
                    <div className="empty-note">
                      {userId
                        ? `No time logged by ${who} in this range yet. Time shows up here when they submit a task with its Time Taken.`
                        : 'No time logged in this range yet. Time shows up here when you submit a task with its Time Taken.'}
                    </div>
                  )}
                </>
              ) : (
                <div className="tl-table-wrap">
                  <table className="tl-table">
                    <thead>
                      <tr><th>Date</th><th style={{ textAlign: 'right' }}>Time spent</th></tr>
                    </thead>
                    <tbody>
                      {data.daily.slice().reverse().map((d) => (
                        <tr key={d.date}>
                          <td>{fmtLong(d.date)}</td>
                          <td style={{ textAlign: 'right' }}>{d.minutes > 0 ? fmtMins(d.minutes) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="tl-two">
              <div className="panel-card tl-panel">
                <div className="tl-panel-head">
                  <div>
                    <h3>Weekly summary</h3>
                    <div className="tl-sub">Last {weekRows.length} weeks · weeks start on Sunday</div>
                  </div>
                </div>
                <BarRows rows={weekRows} current={data.week_start} />
              </div>
              <div className="panel-card tl-panel">
                <div className="tl-panel-head">
                  <div>
                    <h3>Monthly summary</h3>
                    <div className="tl-sub">Last {monthRows.length} months</div>
                  </div>
                </div>
                <BarRows rows={monthRows} current={data.month_start} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
