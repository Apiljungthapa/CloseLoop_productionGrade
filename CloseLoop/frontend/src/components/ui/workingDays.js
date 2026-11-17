// Non-working days for every date a user can pick or read in the app:
//   * Saturday and Sunday — the weekend, for ALL tenants; and
//   * the holidays the Platform Owner has configured (GET /api/holidays), which
//     loadHolidays() fetches once per session.
// Both are shown grey / red and can't be chosen as a task / sub-task / assignee
// due date, and neither ages an overdue task.
//
// Mirrors the backend's `is_non_working_day` / `roll_to_working_day` /
// `working_days_between` (app/calendar, services/due_date_rules.py) — keep the
// two in step. The weekend is `NEPAL_WEEKEND` ({5, 6} in Python's Mon=0
// numbering); here we use JS `getUTCDay()` numbering (Sun=0 .. Sat=6). All values
// are AD "YYYY-MM-DD" strings and the maths is done in UTC so the viewer's
// timezone can't shift a date by a day.
//
// With no holidays loaded (not signed in yet, the request failed, none
// configured) everything below is the weekend-only rule.
import { useSyncExternalStore } from 'react';
import api from '../../api';

const WEEKEND_DOW = [0, 6];
const DAY_MS = 86400000;

let holidayNames = new Map(); // 'YYYY-MM-DD' -> name
let holidayTimes = []; // the same dates as UTC ms, ascending
let version = 0;
const listeners = new Set();

function parseIso(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function toIso(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

const isWeekendMs = (ms) => WEEKEND_DOW.includes(new Date(ms).getUTCDay());
const isNonWorkingMs = (ms) => isWeekendMs(ms) || holidayNames.has(toIso(ms));

// --- the configured holidays -------------------------------------------------

// Replace every configured holiday. `list` is the /api/holidays payload:
// [{ holiday_date: 'YYYY-MM-DD', name, non_working }, ...]. Every recorded
// holiday is a non-working day, exactly like the weekend; `non_working` is the
// server's own answer, so the two never disagree. (An older server that doesn't
// send it only ever skipped category ALL, so that is the fallback.) Two
// holidays on one date are one day (their names are joined).
export function setHolidays(list) {
  const next = new Map();
  (Array.isArray(list) ? list : []).forEach((h) => {
    if (parseIso(h?.holiday_date) === null) return;
    const skipped = h.non_working ?? (h.category || 'ALL').toUpperCase() === 'ALL';
    if (!skipped) return;
    const iso = h.holiday_date.slice(0, 10);
    const name = h.name || 'Holiday';
    const known = next.get(iso);
    next.set(iso, known && !known.split(' / ').includes(name) ? `${known} / ${name}` : known || name);
  });
  holidayNames = next;
  holidayTimes = [...next.keys()].map(parseIso).sort((a, b) => a - b);
  version += 1;
  listeners.forEach((fn) => fn());
}

// Fetch the holidays and apply them. Never throws: on failure the dates simply
// stay weekend-only (the server still enforces holidays on save).
export async function loadHolidays() {
  try {
    const { data } = await api.get('/holidays');
    setHolidays(data);
  } catch {
    /* keep whatever was loaded before */
  }
}

// Changes whenever the holidays do. Call it in a component near the top of the
// tree so everything below re-renders with the new dates.
export function useHolidaysVersion() {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => version,
  );
}

export function holidayNameIso(iso) {
  const ms = parseIso(iso);
  return ms === null ? null : holidayNames.get(toIso(ms)) || null;
}

// [iso, name] for each configured holiday in the 0-based `month` of `year`.
export function holidaysInMonth(year, month) {
  const prefix = `${year}-${String(month + 1).padStart(2, '0')}-`;
  return [...holidayNames.entries()].filter(([iso]) => iso.startsWith(prefix)).sort();
}

// --- non-working days ---------------------------------------------------------

export function isWeekendIso(iso) {
  const ms = parseIso(iso);
  return ms !== null && isWeekendMs(ms);
}

// A weekend day or a configured holiday.
export function isNonWorkingIso(iso) {
  const ms = parseIso(iso);
  return ms !== null && isNonWorkingMs(ms);
}

// Why a day can't be picked, for a tooltip; null for a working day.
export function nonWorkingLabelIso(iso) {
  const name = holidayNameIso(iso);
  if (name) return `${name} (holiday) — not available`;
  return isWeekendIso(iso) ? 'Weekend — not available' : null;
}

// The nearest working date on or before `iso` (Sat/Sun/holiday -> the working
// day before). Returns `iso` unchanged when it is already a working day or is
// not a parseable date.
export function workingDayOnOrBefore(iso) {
  let ms = parseIso(iso);
  if (ms === null) return iso;
  while (isNonWorkingMs(ms)) ms -= DAY_MS;
  return toIso(ms);
}

// Mirror of the backend's `roll_to_working_day` (services/due_date_rules.py):
// the due date the server will actually GENERATE for a date that lands on a
// Saturday, Sunday or holiday — the next working day when `preferLater`, the
// previous one otherwise, never outside the optional `lo` / `hi` bounds (the
// other direction is used when the preferred day would leave them; `iso`
// itself if neither fits). Used by form previews so what they say matches what
// gets generated.
export function rollToWorkingDayIso(iso, { lo = null, hi = null, preferLater = true } = {}) {
  const ms = parseIso(iso);
  if (ms === null || !isNonWorkingMs(ms)) return iso;
  let later = ms;
  while (isNonWorkingMs(later)) later += DAY_MS;
  let earlier = ms;
  while (isNonWorkingMs(earlier)) earlier -= DAY_MS;
  const loMs = lo ? parseIso(lo) : null;
  const hiMs = hi ? parseIso(hi) : null;
  const fits = (c) => (loMs === null || c >= loMs) && (hiMs === null || c <= hiMs);
  const ordered = preferLater ? [later, earlier] : [earlier, later];
  const pick = ordered.find(fits);
  return pick === undefined ? iso : toIso(pick);
}

// Mirror of the backend's `working_days_between`: working days in (start, end]
// — after `start`, up to and including `end`; 0 when `end` isn't after
// `start`. The "days overdue" count: due Friday, read on Monday, is 1; over the
// weekend, or when Monday is a holiday, 0. Whole weeks are closed-form, the
// short tail is checked day by day, and the configured holidays in range come
// off.
export function workingDaysBetweenIso(startIso, endIso) {
  const start = parseIso(startIso);
  const end = parseIso(endIso);
  if (start === null || end === null) return 0;
  const total = Math.round((end - start) / DAY_MS);
  if (total <= 0) return 0;
  let count = Math.floor(total / 7) * 5;
  for (let i = 1; i <= total % 7; i += 1) {
    if (!isWeekendMs(start + i * DAY_MS)) count += 1;
  }
  for (const ms of holidayTimes) {
    if (ms > end) break;
    if (ms > start && !isWeekendMs(ms)) count -= 1;
  }
  return count;
}
