// Timesheet reminder banner - shown on every page while hours are still to be
// sent to the HRMS and a month-end (English AD or Nepali BS) is at hand or
// past. It is not dismissible once a deadline is due or overdue: the reminder
// stays until the timesheet is completed. The server returns nothing while
// reminders are switched off, so this renders nothing then.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { timesheetAPI } from '../apiEntities.js';
import { useOrgPath } from '../orgPath.js';
import './timesheetBanner.css';

const REFRESH_MS = 30 * 60 * 1000;

function hm(total) {
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h}h ${m}m`;
  return h ? `${h}h` : `${m}m`;
}

function headline(o) {
  const cal = o.calendar === 'AD' ? 'English' : 'Nepali';
  if (o.status === 'OVERDUE') {
    return `${cal}-month timesheet overdue by ${o.days_overdue} day${o.days_overdue === 1 ? '' : 's'}`;
  }
  if (o.status === 'DUE') return `${cal}-month timesheet is due today`;
  return `${cal}-month timesheet due in ${o.days_left} day${o.days_left === 1 ? '' : 's'}`;
}

export default function TimesheetBanner() {
  const [items, setItems] = useState([]);
  const path = useOrgPath();

  useEffect(() => {
    let stale = false;
    const pull = () => timesheetAPI.obligations()
      .then((r) => { if (!stale) setItems(Array.isArray(r.data) ? r.data : []); })
      .catch(() => { if (!stale) setItems([]); }); // a banner must never break the page
    pull();
    const timer = setInterval(pull, REFRESH_MS);
    return () => { stale = true; clearInterval(timer); };
  }, []);

  if (items.length === 0) return null;
  const worst = items[0].status;   // the server sorts OVERDUE, DUE, UPCOMING

  return (
    <div className={`ts-banner ts-banner-${worst.toLowerCase()}`} role={worst === 'UPCOMING' ? 'status' : 'alert'}>
      <ul>
        {items.slice(0, 3).map((o) => (
          <li key={`${o.calendar}-${o.period_key}`}>
            <b>{headline(o)}</b> · {o.label} (deadline {o.deadline}) · {o.pending_tasks} submitted task{o.pending_tasks === 1 ? '' : 's'}
            {o.pending_minutes ? ` (${hm(o.pending_minutes)})` : ''} still to send
            {' '}
            <Link to={`${path('/hrms-integration')}?month=${o.calendar === 'AD' ? o.period_key : o.start.slice(0, 7)}`}>
              Open timesheet
            </Link>
          </li>
        ))}
        {items.length > 3 && <li>…and {items.length - 3} more month{items.length - 3 === 1 ? '' : 's'}</li>}
      </ul>
    </div>
  );
}
