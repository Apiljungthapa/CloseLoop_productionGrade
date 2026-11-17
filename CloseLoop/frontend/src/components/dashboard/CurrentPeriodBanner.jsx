// CurrentPeriodBanner — the strip at the top of a dashboard that names the
// period currently running (the one whose date range covers today) so it's
// always obvious which period's work is live. Feeds from GET /periods/active.
import { useCallback, useEffect, useState } from 'react';
import Icon from '../ui/Icon';
import { periodAPI } from '../../apiEntities.js';
import { useLiveRefresh } from '../useLiveRefresh.js';
import { todayIso } from '../../pages/leadDashUtils';

function fmt(iso) {
  if (!iso) return '';
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function daysBetween(aIso, bIso) {
  return Math.round(
    (new Date(`${bIso}T00:00:00`) - new Date(`${aIso}T00:00:00`)) / 86400000,
  );
}

export default function CurrentPeriodBanner({ refreshKey }) {
  const [period, setPeriod] = useState(null);
  const [loaded, setLoaded] = useState(false);

  const fetchActive = useCallback(() => {
    let alive = true;
    periodAPI
      .active()
      .then(({ data }) => { if (alive) setPeriod(data); })
      .catch(() => { if (alive) setPeriod(null); })
      .finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, []);

  useEffect(() => fetchActive(), [fetchActive, refreshKey]);
  useLiveRefresh(fetchActive);

  if (!loaded) return null;

  if (!period) {
    return (
      <div className="current-period-banner is-empty">
        <Icon name="calendar" size={15} />
        <span>No active period — generate a period schedule to begin.</span>
      </div>
    );
  }

  const todayStr = todayIso();
  const totalDays = daysBetween(period.start_date, period.end_date) + 1;
  const dayInto = Math.min(totalDays, Math.max(1, daysBetween(period.start_date, todayStr) + 1));
  const daysLeft = Math.max(0, daysBetween(todayStr, period.end_date));
  const pct = Math.min(100, Math.round((dayInto / totalDays) * 100));
  const closingSoon = daysLeft <= 3;

  return (
    <div className={`current-period-banner${closingSoon ? ' is-closing' : ''}`}>
      <div className="cpb-main">
        <span className="cpb-dot" />
        <div className="cpb-text">
          <div className="cpb-label">
            <span className="cpb-tag">Active period</span>
            <strong>{period.label}</strong>
          </div>
          <div className="cpb-dates">
            {fmt(period.start_date)} → {fmt(period.end_date)}
            {period.bs_start_label && (
              <span className="cpb-bs"> · BS {period.bs_start_label} → {period.bs_end_label}</span>
            )}
          </div>
        </div>
      </div>
      <div className="cpb-right">
        <div className="cpb-progress" title={`Day ${dayInto} of ${totalDays}`}>
          <div className="cpb-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="cpb-countdown">
          {daysLeft === 0
            ? 'Closes today'
            : `${daysLeft} day${daysLeft === 1 ? '' : 's'} left`}
        </div>
      </div>
    </div>
  );
}
