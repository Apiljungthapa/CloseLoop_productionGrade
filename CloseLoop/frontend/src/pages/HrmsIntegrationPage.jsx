// "HRMS Integration" - the automated timesheet.
//
// Pick an English (AD) month and an HRMS project. CloseLoop lists EVERY date of the
// month and, under each, the tasks and sub-tasks YOU SUBMITTED that day (and your own
// part of a group task). The time-log table is never read. Each is matched to an HRMS
// activity and shown for review:
//
//   * hours are the Time Taken you entered when you submitted; if you left it blank the
//     hours box is empty and you type them - nothing is invented
//   * confident matches are pre-selected; anything unsure is left for you
//   * an activity with sub-activities opens a field for each one
//   * entries already on your HRMS timesheet are flagged and can't be re-sent
//   * "Review & submit" lists exactly what will be sent before anything is
//
// Everyone fills their own timesheet from their own submitted tasks. The server
// re-checks everything on submit.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Modal } from 'react-bootstrap';
import Icon from '../components/ui/Icon';
import HrmsProjectSelect from '../components/ui/HrmsProjectSelect';
import { getErrorMessage } from '../api';
import { hrmsAPI, timesheetAPI } from '../apiEntities.js';
import './hrmsIntegration.css';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

// Flags that make a line unsendable (mirrors the server's blocking flags).
const BLOCKING = new Set([
  'ALREADY_IN_HRMS', 'ALREADY_SENT_FROM_CLOSELOOP', 'DAY_ALREADY_SENT_FROM_CLOSELOOP',
  'DAY_OVER_24_HOURS',
]);
const FLAG_TEXT = {
  ALREADY_IN_HRMS: 'Already on your HRMS timesheet',
  ALREADY_SENT_FROM_CLOSELOOP: 'Already sent from CloseLoop',
  DAY_ALREADY_SENT_FROM_CLOSELOOP: 'This date was already sent (a day is sent once)',
  HRMS_HAS_OTHER_ENTRIES_THAT_DAY: 'HRMS has other entries this day',
  DAY_OVER_24_HOURS: 'More than 24 hours that day',
  SUMMARY_SHORTENED: 'Summary was shortened - check it',
};
const WORK_TYPES = [
  ['BILLABLE', 'Billable'], ['NON_BILLABLE', 'Non-billable'], ['PAID_NON_BILLABLE', 'Paid non-billable'],
];
const WORK_TYPE_TEXT = Object.fromEntries(WORK_TYPES);

// An activity's sub activities are extra FIELDS the HRMS wants a value for (not
// alternatives). Fields that are obviously a description are filled from the
// summary; the rest the user types. Mirrors the server (timesheet_builder).
const DESCRIPTION_LIKE = new Set(['description', 'summary', 'details', 'detail', 'remarks', 'remark', 'notes', 'note', 'comment', 'comments']);
const norm = (t) => String(t || '').toLowerCase().replace(/[^0-9a-z]+/g, ' ').trim();
function prefillValues(act, summary) {
  const out = {};
  (act?.sub_activities || []).forEach((f) => {
    out[f.id] = DESCRIPTION_LIKE.has(norm(f.name)) || DESCRIPTION_LIKE.has(norm(f.key)) ? summary : '';
  });
  return out;
}
const valuesComplete = (act, values) => !!act && act.sub_activities.every((f) => String(values[f.id] || '').trim());
// Hours typed into a row as text ("1.5", "1,5") -> whole minutes; 0 = nothing usable (max 24 h).
function typedMinutes(text) {
  const h = parseFloat(String(text ?? '').replace(',', '.'));
  return Number.isFinite(h) && h > 0 && h <= 24 ? Math.round(h * 60) : 0;
}

// 90 -> "1h 30m", 45 -> "45m", 120 -> "2h"
function formatMinutes(total) {
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  if (hh && mm) return `${hh}h ${mm}m`;
  return hh ? `${hh}h` : `${mm}m`;
}
const fmtHours = (h) => `${Number(h).toFixed(2)} h`;

// 'YYYY-MM-DD' -> "Mon, 05 Oct 2026" (parsed as a local date, so no timezone shift)
function formatDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
  });
}

const monthKey = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
const newKey = () => (globalThis.crypto?.randomUUID
  ? globalThis.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`);

function NativeSelect({ id, value, onChange, options, label, className = '' }) {
  return (
    <div className={`hrms-select ${className}`}>
      <select id={id} value={value ?? ''} aria-label={label} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

export default function HrmsIntegrationPage() {
  const now = new Date();
  const [params] = useSearchParams();
  const fromUrl = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(params.get('month') || '');

  const [year, setYear] = useState(fromUrl ? Number(fromUrl[1]) : now.getFullYear());
  const [month, setMonth] = useState(fromUrl ? Number(fromUrl[2]) : now.getMonth() + 1);
  const [through, setThrough] = useState(/^\d{4}-\d{2}-\d{2}$/.test(params.get('through') || '') ? params.get('through') : '');
  const [projectId, setProjectId] = useState('');
  const [projectName, setProjectName] = useState('');

  const [activities, setActivities] = useState([]);
  const [load, setLoad] = useState({ status: 'idle', error: '', data: null });
  const [history, setHistory] = useState([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [edits, setEdits] = useState({}); // line key -> { selected, activityId, subValues, summary, workType }
  const [notice, setNotice] = useState(null); // { text, error? }

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [ack, setAck] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [modalError, setModalError] = useState('');
  const idemKey = useRef('');
  const [verifying, setVerifying] = useState('');

  const mKey = monthKey(year, month);

  // The HRMS activity list (shared by every row's dropdown).
  useEffect(() => {
    if (!projectId) { setActivities([]); return undefined; }
    let stale = false;
    hrmsAPI.activities()
      .then((r) => { if (!stale) setActivities(Array.isArray(r.data) ? r.data : []); })
      .catch(() => { if (!stale) setActivities([]); });
    return () => { stale = true; };
  }, [projectId]);

  // The proposed timesheet for this project + month.
  useEffect(() => {
    if (!projectId) { setLoad({ status: 'idle', error: '', data: null }); return undefined; }
    let stale = false;
    setLoad((s) => ({ status: 'loading', error: '', data: s.data && s.data.hrms_project_id === Number(projectId) ? s.data : null }));
    setEdits({});
    const q = { hrms_project_id: projectId, month: mKey, ...(through ? { through } : {}) };
    Promise.all([
      timesheetAPI.preview(q),
      timesheetAPI.submissions({ hrms_project_id: projectId, month: mKey }).catch(() => ({ data: [] })),
    ])
      .then(([p, h]) => {
        if (stale) return;
        setLoad({ status: 'ready', error: '', data: p.data });
        setHistory(h.data || []);
      })
      .catch((e) => {
        if (stale) return;
        setLoad({ status: 'error', error: getErrorMessage(e, 'Could not build the timesheet.'), data: null });
      });
    return () => { stale = true; };
  }, [projectId, mKey, through, reloadKey]);

  const data = load.data;
  const actById = useMemo(() => new Map(activities.map((a) => [a.id, a])), [activities]);
  const existingKeys = useMemo(
    () => new Set((data?.existing_entries || []).map((e) => `${e.work_date}|${e.hrms_activity_id}`)),
    [data],
  );
  const canSubmit = !!data?.can_submit;

  // Lines as the user currently has them: server proposal + their changes.
  const rows = useMemo(() => (data?.lines || []).map((l) => {
    const e = edits[l.key] || {};
    const activityId = e.activityId !== undefined ? e.activityId : l.hrms_activity_id;
    const act = activityId ? actById.get(activityId) : null;
    const summary = e.summary !== undefined ? e.summary : l.summary;
    const fields = act ? act.sub_activities : [];
    const values = e.subValues !== undefined ? e.subValues : (l.sub_values || {});
    const complete = valuesComplete(act, values);
    const hardFlag = l.flags.some((f) => BLOCKING.has(f));
    const dup = complete && existingKeys.has(`${l.work_date}|${activityId}`);
    const blocked = hardFlag || dup;
    // Time Taken left blank at submission: no hours until the user types them.
    const hoursText = e.hours !== undefined ? e.hours : '';
    const minutes = l.needs_hours ? typedMinutes(hoursText) : l.minutes;
    const needsHours = l.needs_hours && minutes <= 0;
    const selectable = canSubmit && complete && !blocked && !needsHours;
    const touched = e.activityId !== undefined || e.subValues !== undefined || e.summary !== undefined
      || e.workType !== undefined || e.hours !== undefined;
    const selected = selectable && (e.selected !== undefined ? e.selected : l.selected);
    // A line the server held for empty fields reads as "chosen by you" once they are filled.
    const status = l.status === 'NEEDS_SUB' && complete ? 'MATCHED' : l.status;
    return {
      ...l, activityId, act, fields, values, complete, blocked, dup, selectable, selected, status, summary,
      minutes, hours: minutes / 60, hoursText, needsHours,
      workType: e.workType || l.work_type,
      byHand: touched || l.status !== 'MATCHED',
    };
  }), [data, edits, actById, existingKeys, canSubmit]);

  // Every date of the period, each with the tasks submitted that day (empty days included).
  const dayGroups = useMemo(() => {
    const byDate = new Map();
    rows.forEach((r) => { byDate.set(r.work_date, [...(byDate.get(r.work_date) || []), r]); });
    return (data?.days || []).map((d) => ({ ...d, rows: byDate.get(d.date) || [] }));
  }, [data, rows]);

  const selectedRows = useMemo(() => rows.filter((r) => r.selected), [rows]);
  const selectedMinutes = selectedRows.reduce((t, r) => t + r.minutes, 0);
  // A POST-only HRMS can't be read back: say so and make the user confirm.
  const hrmsUnchecked = (data?.warnings || []).some((w) => w.code === 'HRMS_ENTRIES_NOT_CHECKED');
  // The HRMS looks the person up by e-mail and may refuse if they aren't set up there.
  const hrmsUnlisted = (data?.warnings || []).some((w) => w.code === 'NOT_A_PROJECT_MEMBER');
  const hasTyped = selectedRows.some((r) => r.needs_hours);
  const needsAck = hrmsUnchecked || hrmsUnlisted || hasTyped || selectedRows.some((r) => r.flags.length > 0 || r.byHand);

  const setEdit = (key, patch) => setEdits((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  const toggle = (r) => setEdit(r.key, { selected: !r.selected });
  const pickActivity = (r, value) => {
    const id = value ? Number(value) : null;
    const act = id ? actById.get(id) : null;
    const subValues = prefillValues(act, r.summary);
    // Picking an activity is a deliberate choice: select the line unless its extra fields still need values.
    setEdit(r.key, { activityId: id, subValues, selected: !!act && valuesComplete(act, subValues) });
  };
  const setHours = (r, text) => {
    const m = typedMinutes(text);
    // Typing hours on a row that is otherwise ready is the decision to send it.
    setEdit(r.key, { hours: text, selected: m > 0 && r.complete && !r.blocked && canSubmit });
  };
  const setValue = (r, fieldId, text) => {
    const subValues = { ...r.values, [fieldId]: text };
    setEdit(r.key, { subValues, selected: valuesComplete(r.act, subValues) });
  };

  const allSelectable = rows.filter((r) => r.selectable);
  const allSelected = allSelectable.length > 0 && allSelectable.every((r) => r.selected);
  const toggleAll = () => setEdits((prev) => {
    const next = { ...prev };
    allSelectable.forEach((r) => { next[r.key] = { ...next[r.key], selected: !allSelected }; });
    return next;
  });

  // The HRMS takes one request per DAY: a date's lines go together, lines on the same
  // activity merge into one entry, and the day has ONE summary.
  const days = useMemo(() => {
    const byDate = new Map();
    selectedRows.forEach((r) => {
      const day = byDate.get(r.work_date) || { date: r.work_date, items: new Map(), summaries: [], minutes: 0 };
      const item = day.items.get(r.activityId) || { act: r.act, workType: r.workType, minutes: 0, values: {} };
      item.minutes += r.minutes;
      Object.entries(r.values).forEach(([k, v]) => { if (!item.values[k] && v) item.values[k] = v; });
      day.items.set(r.activityId, item);
      day.minutes += r.minutes;
      if (r.summary && !day.summaries.includes(r.summary)) day.summaries.push(r.summary);
      byDate.set(r.work_date, day);
    });
    return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
      .map((d) => ({ ...d, items: [...d.items.values()] }));
  }, [selectedRows]);

  const openConfirm = () => {
    idemKey.current = newKey();
    setAck(false);
    setModalError('');
    setConfirmOpen(true);
  };

  const submit = async () => {
    if (submitting || selectedRows.length === 0) return;
    setSubmitting(true);
    setModalError('');
    try {
      const { data: sub } = await timesheetAPI.submit({
        hrms_project_id: Number(projectId),
        month: mKey,
        through: through || null,
        idempotency_key: idemKey.current,
        acknowledge_warnings: ack,
        lines: selectedRows.map((r) => ({
          key: r.key,
          hrms_activity_id: r.activityId,
          sub_values: r.values,
          summary: r.summary,
          work_type: r.workType,
          ...(r.needs_hours ? { minutes: r.minutes } : {}),
        })),
      });
      const sent = sub.lines.filter((l) => l.status === 'SENT').length;
      const unknown = sub.lines.filter((l) => l.status === 'UNCERTAIN').length;
      const failed = sub.lines.filter((l) => l.status === 'FAILED').length;
      setConfirmOpen(false);
      setNotice({
        text: `${sent} of ${sub.lines.length} entries sent to the HRMS.`
          + (failed ? ` ${failed} were refused - they are back in the list.` : '')
          + (unknown ? ` ${unknown} have an unknown result - use Verify below before sending again.` : ''),
        error: failed > 0 || unknown > 0,
      });
      setReloadKey((k) => k + 1);
    } catch (e) {
      const code = e?.response?.data?.detail?.code;
      setModalError(getErrorMessage(e, 'Could not submit to the HRMS.'));
      // The screen no longer matches reality - make the user look again.
      if (code === 'STALE_PREVIEW' || code === 'LINE_HELD' || code === 'DUPLICATE_ENTRY') setReloadKey((k) => k + 1);
    } finally {
      setSubmitting(false);
    }
  };

  const verify = async (id) => {
    setVerifying(id);
    try {
      await timesheetAPI.verify(id);
      setNotice({ text: 'Checked the HRMS - the submission now shows what actually arrived.' });
      setReloadKey((k) => k + 1);
    } catch (e) {
      setNotice({ text: getErrorMessage(e, 'Could not check the HRMS.'), error: true });
    } finally {
      setVerifying('');
    }
  };

  const resolve = async (id, outcome) => {
    const sure = outcome === 'SENT'
      ? 'Mark these entries as SENT? Only do this if you saw them in the HRMS.'
      : 'Mark these entries as NOT sent? They go back on the list and can be sent again - if they are in the HRMS this will duplicate them.';
    if (!window.confirm(sure)) return;
    setVerifying(id);
    try {
      await timesheetAPI.resolve(id, outcome);
      setNotice({ text: outcome === 'SENT' ? 'Marked as sent.' : 'Marked as not sent - they are back in the list.' });
      setReloadKey((k) => k + 1);
    } catch (e) {
      setNotice({ text: getErrorMessage(e, 'Could not update the submission.'), error: true });
    } finally {
      setVerifying('');
    }
  };

  const years = Array.from({ length: 5 }, (_, i) => now.getFullYear() - i);
  const monthOptions = MONTHS.map((m, i) => ({
    value: i + 1, label: m, disabled: year === now.getFullYear() && i + 1 > now.getMonth() + 1,
  }));
  const onYear = (v) => {
    const y = Number(v);
    setYear(y);
    if (y === now.getFullYear() && month > now.getMonth() + 1) setMonth(now.getMonth() + 1);
  };
  const monthLabel = `${MONTHS[month - 1]} ${year}`;
  const t = data?.totals;
  const activityLabel = (act) => (act ? act.name : '');

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="hrms-card">
          <div className="hrms-head">
            <h1>HRMS Integrations</h1>
            <p>Every day of the month with the tasks you submitted, matched to HRMS activities. Review it, then send it</p>
          </div>

          <div className="hrms-filters">
            <div className="hrms-field">
              <label htmlFor="hrms-month">Month<span className="req">*</span></label>
              <NativeSelect id="hrms-month" label="Month" value={month} options={monthOptions} onChange={(v) => setMonth(Number(v))} />
            </div>
            <div className="hrms-field">
              <label htmlFor="hrms-year">Year<span className="req">*</span></label>
              <NativeSelect
                id="hrms-year" label="Year" value={year}
                options={years.map((y) => ({ value: y, label: String(y) }))} onChange={onYear}
              />
            </div>
            <div className="hrms-field hrms-field-project">
              <label htmlFor="hrms-project">Project<span className="req">*</span></label>
              <HrmsProjectSelect
                id="hrms-project" value={projectId}
                onChange={(id, p) => { setProjectId(id); setProjectName(p.name); setNotice(null); }}
              />
            </div>
          </div>

          {projectId && (
            <div className="ts-through">
              <label htmlFor="ts-through">Only up to (optional)</label>
              <input
                id="ts-through" type="date" value={through}
                min={`${mKey}-01`} onChange={(e) => setThrough(e.target.value)}
              />
              {through && <button type="button" className="ts-linkbtn" onClick={() => setThrough('')}>Whole month</button>}
              <span className="ts-hint">Use this to send the days up to a Nepali month-end, then the rest later.</span>
            </div>
          )}

          {!projectId && (
            <div className="hrms-empty">
              <Icon name="list-dots" size={28} />
              <p>Select a project to build your timesheet for {monthLabel}.</p>
            </div>
          )}

          {projectId && (
            <section className="hrms-list" aria-label={`${projectName} timesheet, ${monthLabel}`}>
              <div className="hrms-list-head">
                <h2>{projectName} <span>· {monthLabel}{through ? ` (up to ${formatDate(through)})` : ''}</span></h2>
              </div>

              {load.status === 'loading' && (
                <div className="hrms-empty" role="status">
                  <span className="hrms-dd-spin" aria-hidden="true" />
                  <p>Building your timesheet…</p>
                </div>
              )}

              {load.status === 'error' && (
                <div className="hrms-empty hrms-empty-err" role="alert">
                  <p>{load.error}</p>
                  <button type="button" className="hrms-btn-outline" onClick={() => setReloadKey((k) => k + 1)}>Retry</button>
                </div>
              )}

              {data && (
                <>
                  {data.blockers.map((b) => (
                    <div key={b.code} className="ts-alert ts-alert-block" role="alert">
                      <Icon name="alert-triangle" size={16} /> <span>{b.message}</span>
                    </div>
                  ))}
                  {data.warnings.map((w) => (
                    <div key={w.code} className="ts-alert ts-alert-warn">
                      <Icon name="alert-triangle" size={16} /> <span>{w.message}</span>
                    </div>
                  ))}

                  {rows.length === 0 && load.status === 'ready' && t && t.already_filed_tasks > 0 && (
                    <div className="hrms-empty">
                      <p>Everything you submitted in {monthLabel} has already been sent.</p>
                    </div>
                  )}

                  {dayGroups.length > 0 && (
                    <div className="hrms-table-wrap">
                      <table className="hrms-table ts-table">
                        <thead>
                          <tr>
                            <th scope="col" className="col-select">
                              <input
                                type="checkbox" className="hrms-check" checked={allSelected}
                                disabled={allSelectable.length === 0} onChange={toggleAll}
                                aria-label="Select all sendable lines"
                              />
                            </th>
                            <th scope="col" className="col-work">CloseLoop work</th>
                            <th scope="col" className="col-act">HRMS activity</th>
                            <th scope="col" className="col-time">Hours</th>
                            <th scope="col" className="col-type">Work type</th>
                            <th scope="col" className="col-sum">Summary</th>
                          </tr>
                        </thead>
                        {dayGroups.map((d) => {
                          const dayMinutes = d.rows.reduce((sum, r) => sum + r.minutes, 0);
                          return (
                            <tbody
                              key={d.date}
                              className={`ts-daygroup${d.non_working ? ' is-off' : ''}${d.rows.length === 0 ? ' is-empty' : ''}`}
                            >
                              <tr className="ts-day">
                                <th colSpan={6} scope="rowgroup">
                                  <div className="ts-day-row">
                                    <span className="ts-day-date">{formatDate(d.date)}</span>
                                    {d.label && <span className="ts-day-tag">{d.label}</span>}
                                    <span className="ts-day-count">
                                      {d.rows.length === 0 ? 'Nothing submitted' : `${d.rows.length} task${d.rows.length === 1 ? '' : 's'}`}
                                    </span>
                                    {dayMinutes > 0 && <span className="ts-day-total">{formatMinutes(dayMinutes)}</span>}
                                  </div>
                                </th>
                              </tr>
                              {d.rows.map((r) => (
                                <TimesheetRow
                                  key={r.key} r={r} activities={activities} canSubmit={canSubmit}
                                  onToggle={() => toggle(r)} onActivity={(v) => pickActivity(r, v)}
                                  onValue={(id, v) => setValue(r, id, v)} onHours={(v) => setHours(r, v)}
                                  onType={(v) => setEdit(r.key, { workType: v })}
                                  onSummary={(v) => setEdit(r.key, { summary: v })}
                                />
                              ))}
                            </tbody>
                          );
                        })}
                      </table>
                    </div>
                  )}

                  {rows.length > 0 && (
                    <div className="hrms-actions">
                      {notice ? (
                        <span className={`hrms-saved${notice.error ? ' err' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</span>
                      ) : (
                        <span className="hrms-selected" aria-live="polite">
                          {selectedRows.length > 0
                            ? `${selectedRows.length} line${selectedRows.length === 1 ? '' : 's'} selected · ${formatMinutes(selectedMinutes)}`
                            : canSubmit ? 'Select the lines to send' : 'Review only - sending is not available right now'}
                        </span>
                      )}
                      <button type="button" className="hrms-save" disabled={!canSubmit || selectedRows.length === 0} onClick={openConfirm}>
                        Review &amp; submit
                      </button>
                    </div>
                  )}
                  {rows.length === 0 && notice && (
                    <div className="hrms-actions">
                      <span className={`hrms-saved${notice.error ? ' err' : ''}`} role="status">{notice.text}</span>
                    </div>
                  )}

                  {data.existing_entries.length > 0 && (
                    <div className="ts-panel">
                      <h3>Already on your timesheet ({data.existing_entries.length})</h3>
                      <ul>
                        {data.existing_entries.map((e, i) => (
                          <li key={`${e.work_date}-${e.hrms_activity_id}-${e.origin}-${i}`}>
                            {formatDate(e.work_date)} · {activityLabel(actById.get(e.hrms_activity_id)) || `Activity #${e.hrms_activity_id}`}
                            {' '}· {fmtHours(e.hours)} <span className="ts-hint">({e.origin === 'HRMS' ? 'in the HRMS' : 'sent from CloseLoop'})</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {history.length > 0 && (
                    <div className="ts-panel">
                      <h3>Submissions for {monthLabel}</h3>
                      <ul>
                        {history.map((s) => (
                          <li key={s.id}>
                            {new Date(s.created_at).toLocaleString('en-GB')} · <b className={`ts-status ts-status-${String(s.status).toLowerCase()}`}>{s.status}</b> · {s.lines.length} entr{s.lines.length === 1 ? 'y' : 'ies'}
                            {s.lines.some((l) => l.status === 'UNCERTAIN') && (data.hrms_entries_readable ? (
                              <button type="button" className="ts-linkbtn" disabled={verifying === s.id} onClick={() => verify(s.id)}>
                                <Icon name="refresh-cw" size={12} /> {verifying === s.id ? 'Checking…' : 'Verify with HRMS'}
                              </button>
                            ) : (
                              <span className="ts-resolve">
                                <span className="ts-hint"> Result unknown - check your HRMS, then: </span>
                                <button type="button" className="ts-linkbtn" disabled={verifying === s.id} onClick={() => resolve(s.id, 'SENT')}>It arrived</button>
                                <button type="button" className="ts-linkbtn" disabled={verifying === s.id} onClick={() => resolve(s.id, 'NOT_SENT')}>It didn't arrive</button>
                              </span>
                            ))}
                            {s.lines.filter((l) => l.status === 'FAILED' && l.error).slice(0, 2).map((l) => (
                              <span key={l.id} className="ts-hint"> · {formatDate(l.work_date)}: {l.error}</span>
                            ))}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </section>
          )}
        </div>
      </div>

      <Modal
        show={confirmOpen} onHide={() => !submitting && setConfirmOpen(false)}
        className="ts-modal" size="xl" centered scrollable
      >
        <Modal.Header closeButton={!submitting}>
          <Modal.Title>
            Review &amp; confirm
            <span className="ts-modal-sub">{projectName} · {monthLabel}</span>
          </Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <div className="ts-confirm-tiles">
            <div className="ts-confirm-tile"><b>{days.length}</b><span>day{days.length === 1 ? '' : 's'} to send</span></div>
            <div className="ts-confirm-tile"><b>{selectedRows.length}</b><span>line{selectedRows.length === 1 ? '' : 's'} selected</span></div>
            <div className="ts-confirm-tile"><b>{formatMinutes(selectedMinutes)}</b><span>total time</span></div>
          </div>
          <p className="ts-confirm-note">
            These will be added to your HRMS timesheet - each day is sent once, with one summary. Lines on the same
            date and activity are sent as one entry. Nothing is sent until you confirm.
          </p>
          <div className="ts-confirm-wrap">
            <table className="ts-confirm-table">
              <thead>
                <tr>
                  <th className="cf-date">Date</th><th>Activities</th><th className="cf-hours">Hours</th><th>Day summary</th>
                </tr>
              </thead>
              <tbody>
                {days.map((d) => (
                  <tr key={d.date}>
                    <td className="cf-date">{formatDate(d.date)}</td>
                    <td>
                      {d.items.map((it) => (
                        <div key={it.act?.id} className="ts-day-item">
                          <b>{activityLabel(it.act)}</b>
                          <span className="ts-day-meta">
                            {fmtHours(Math.round((it.minutes / 60) * 100) / 100)} · {WORK_TYPE_TEXT[it.workType]}
                          </span>
                          {it.act?.sub_activities.map((f) => (
                            <div key={f.id} className="ts-hint">{f.name}: {it.values[f.id]}</div>
                          ))}
                        </div>
                      ))}
                    </td>
                    <td className="cf-hours">{fmtHours(Math.round((d.minutes / 60) * 100) / 100)}</td>
                    <td className="cf-summary">{d.summaries.join(' | ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {needsAck && (
            <label className="ts-ack">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              {[
                'I reviewed the lines above.',
                hasTyped && 'I typed the hours for tasks submitted without a Time Taken.',
                hrmsUnchecked && "I know CloseLoop can't see my HRMS timesheet, so I have checked there for entries I added myself.",
                hrmsUnlisted && 'I know the HRMS may refuse this if my e-mail is not set up there.',
              ].filter(Boolean).join(' ')}
            </label>
          )}
          {modalError && <div className="ts-alert ts-alert-block" role="alert"><Icon name="alert-triangle" size={16} /> <span>{modalError}</span></div>}
        </Modal.Body>
        <Modal.Footer>
          <button type="button" className="hrms-btn-outline" disabled={submitting} onClick={() => setConfirmOpen(false)}>Back</button>
          <button type="button" className="hrms-save" disabled={submitting || (needsAck && !ack)} onClick={submit}>
            {submitting ? 'Sending…' : `Confirm & send to HRMS (${formatMinutes(selectedMinutes)})`}
          </button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}

function TimesheetRow({ r, activities, canSubmit, onToggle, onActivity, onValue, onHours, onType, onSummary }) {
  const suggestions = (r.suggestions || []).filter((s) => activities.some((a) => a.id === s.activity_id));
  const suggestedIds = new Set(suggestions.map((s) => s.activity_id));
  const work = [];
  const seen = new Set();
  r.sources.forEach((s) => {
    const label = s.subtask_title ? `${s.task_title} › ${s.subtask_title}` : s.task_title;
    if (!seen.has(label)) { seen.add(label); work.push(label); }
  });
  const tone = r.blocked ? 'blocked' : 'ok';
  return (
    <tr className={`ts-row ts-${tone}${r.selected ? ' is-selected' : ''}${r.needs_hours ? ' is-placeholder' : ''}`}>
      <td className="col-select">
        <input
          type="checkbox" className="hrms-check" checked={r.selected} disabled={!r.selectable}
          onChange={onToggle} aria-label={`Select ${formatDate(r.work_date)}`}
        />
      </td>
      <td className="ts-work">
        {work.slice(0, 3).map((w) => <div key={w} title={w}>{w}</div>)}
        {work.length > 3 && <div className="ts-hint">+{work.length - 3} more</div>}
      </td>
      <td className="ts-act">
        <select
          className="ts-select" value={r.activityId ?? ''} disabled={!canSubmit && !r.activityId}
          aria-label={`HRMS activity for ${formatDate(r.work_date)}`} onChange={(e) => onActivity(e.target.value)}
        >
          <option value="">Choose activity…</option>
          {suggestions.length > 0 && (
            <optgroup label="Suggested">
              {suggestions.map((s) => {
                const a = activities.find((x) => x.id === s.activity_id);
                return <option key={`s${s.activity_id}`} value={s.activity_id}>{a.name}</option>;
              })}
            </optgroup>
          )}
          <optgroup label="All activities">
            {activities.filter((a) => !suggestedIds.has(a.id) || r.activityId === a.id).map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </optgroup>
        </select>
        {r.fields.length > 0 && (
          <div className="ts-fields">
            {r.fields.map((f) => (
              <label key={f.id} className="ts-field">
                <span>{f.name}</span>
                <input
                  type="text" value={r.values[f.id] || ''} disabled={!canSubmit}
                  placeholder={f.description || f.key || 'Value'}
                  aria-label={`${f.name} for ${formatDate(r.work_date)}`} onChange={(e) => onValue(f.id, e.target.value)}
                />
              </label>
            ))}
          </div>
        )}
        <div className="ts-chips">
          {r.needs_hours && <span className="ts-chip ts-chip-note">No Time Taken entered</span>}
          {r.dup && !r.flags.includes('ALREADY_IN_HRMS') && <span className="ts-chip ts-chip-flag">{FLAG_TEXT.ALREADY_IN_HRMS}</span>}
          {r.flags.map((f) => (
            <span key={f} className={`ts-chip ${BLOCKING.has(f) ? 'ts-chip-flag' : 'ts-chip-note'}`}>
              {FLAG_TEXT[f] || f}{f === 'ALREADY_IN_HRMS' && r.existing_hours != null ? ` (${fmtHours(r.existing_hours)})` : ''}
            </span>
          ))}
        </div>
      </td>
      <td className="col-time" title={r.needs_hours ? 'Type the hours you worked' : `Time Taken: ${r.minutes} minutes`}>
        {r.needs_hours ? (
          <>
            <input
              type="number" className="ts-hours" min="0" max="24" step="0.25" inputMode="decimal"
              value={r.hoursText} placeholder="0.00" disabled={!canSubmit}
              aria-label={`Hours worked, ${formatDate(r.work_date)}`} onChange={(e) => onHours(e.target.value)}
            />
            <div className="ts-hint">Enter hours (h)</div>
          </>
        ) : (
          <>
            <span className="ts-hours-val">{fmtHours(r.hours)}</span>
            <div className="ts-hint">{formatMinutes(r.minutes)}</div>
          </>
        )}
      </td>
      <td className="ts-typecell">
        <select className="ts-select ts-type" value={r.workType} aria-label="Work type" onChange={(e) => onType(e.target.value)} disabled={!canSubmit}>
          {WORK_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </td>
      <td className="ts-summary-cell">
        <textarea
          className="ts-summary" rows={2} value={r.summary} disabled={!canSubmit}
          aria-label={`Summary for ${formatDate(r.work_date)}`} onChange={(e) => onSummary(e.target.value)}
        />
      </td>
    </tr>
  );
}
