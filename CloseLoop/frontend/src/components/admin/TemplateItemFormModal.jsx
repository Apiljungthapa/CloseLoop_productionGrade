import { useEffect, useMemo, useState } from 'react';
import Modal from '../ui/Modal';
import DateInput from '../ui/DateInput';
import TagsInput from '../ui/TagsInput';
import { periodAPI } from '../../apiEntities.js';
import { toLocalIso, fmtDate, useBsDateLabels } from '../../pages/leadDashUtils';
import { rollToWorkingDayIso } from '../ui/workingDays';

const ANCHORS = [
  { value: 'PERIOD_START', label: 'from period start' },
  { value: 'PERIOD_END', label: 'from period end' },
];

const addDaysIso = (iso, n) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + (Number(n) || 0));
  return d;
};

const daysBetween = (a, b) =>
  Math.round((new Date(`${b}T00:00:00`) - new Date(`${a}T00:00:00`)) / 86400000);

// "Repeat every N ..." can never be 0 (or blank, or negative) — there's no
// such thing as "every 0 months". Clamping in the input itself, not just
// wherever `interval` gets read later, so the field never even shows an
// invalid value the user would have to notice got silently overridden.
const clampInterval = (raw) => {
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
};

const FREQUENCIES = [
  { value: '', label: 'Once per period' },
  { value: 'DAILY', label: 'Daily' },
  { value: 'WEEKLY', label: 'Weekly' },
  { value: 'MONTHLY', label: 'Monthly' },
  { value: 'QUARTERLY', label: 'Quarterly' },
  { value: 'SEMI_ANNUAL', label: 'Semi-annual' },
  { value: 'ANNUAL', label: 'Annual' },
  { value: 'CUSTOM', label: 'Custom…' },
];
// "Custom" is a UI shell over (base frequency + interval_count). Each unit
// maps to the real RecurrenceFrequency the backend understands; the
// interval multiplies its step (every N months / quarters / …).
const CUSTOM_UNITS = [
  { value: 'DAILY', label: 'day(s)' },
  { value: 'WEEKLY', label: 'week(s)' },
  { value: 'MONTHLY', label: 'month(s)' },
  { value: 'QUARTERLY', label: 'quarter(s)' },
  { value: 'SEMI_ANNUAL', label: 'half-year(s)' },
  { value: 'ANNUAL', label: 'year(s)' },
];
// Saturday and Sunday are weekends for every tenant — the backend never
// schedules a Daily/Weekly occurrence on them, so they're not offered.
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];

const PRIORITIES = [
  { value: 'LOW', cls: 'p-low', label: 'Low' },
  { value: 'MEDIUM', cls: 'p-med', label: 'Medium' },
  { value: 'HIGH', cls: 'p-high', label: 'High' },
];

// Add form for a task template or a sub-task template — both get the
// IDENTICAL scheduling section:
//   • "Once per period"  -> Due Day = day N of every period.
//   • Any frequency      -> First Due Date = the first occurrence; every later
//     occurrence is computed forward from it and dropped into whichever
//     period it falls in. For a sub-task this multiplies UNDER EACH
//     occurrence of its parent task (e.g. a DAILY sub-task under one
//     MONTHLY task -> ~30 sub-task rows for that one task) — ignored when
//     the parent task is itself DAILY/WEEKLY (already multiplies 1:1).
// A sub-task always sets its OWN frequency/due day now — it no longer
// inherits the parent task's due date by leaving it blank — but it stays
// constrained by the parent task's due date: this form blocks (and the
// backend authoritatively rejects, 400) picking a sub-task schedule that
// would land after the parent task's own due date. That's only ever
// checkable up front when NEITHER side recurs (`parentTask` is passed only
// for a sub-task, and only ever a plain TaskTemplateOut); a recurring
// parent/sub-task pairing is a moving target across periods, so that case
// is enforced by clamping at generation time instead (see
// plan_generation_service._subtask_due).
// `isTask` gates the task-only extras: Reviewer required, Priority, Tags.
// `initial` (a TaskTemplateOut / SubTaskTemplateOut) switches the form to
// EDIT mode — every field is pre-filled and the parent's onSubmit should
// PATCH rather than POST.
//
// `mode="live"` reuses this exact same form/component for a Team Lead's
// ad-hoc "Add Task" Subtasks section (only ever with isTask=false — the
// ad-hoc Task itself keeps its own existing form) instead of a Close Pack
// SubTaskTemplate:
//   * Adds an Assignee picker (`members`) — a template has no assignee,
//     but a live sub-task needs one at creation.
//   * Always shows Priority/Tags (a live SubTask supports both; a
//     SubTaskTemplate deliberately doesn't — see its model docstring).
//   * Takes the period bounds directly (`periodStart`/`periodEnd` — the
//     ad-hoc Task's own plan/period) instead of fetching the tenant's
//     currently-running period, and the parent's own due date directly
//     (`parentDueDate`, a plain ISO date — the ad-hoc Task's own chosen
//     due date) instead of resolving one from a TaskTemplateOut.
//   * A live sub-task has no independent recurrence yet (only the whole
//     ad-hoc Task can recur) — Frequency still resolves the SAME Day/
//     anchor or Start-date fields to a single concrete date, but that's
//     the one and only due date saved; it does not multiply into several
//     rows the way a Close Pack Subtask Template does at generation time.
//   * `onSubmit` receives `{ title, description, assigned_to_id, due_date,
//     due_time, priority, tags }` (a TaskSubtaskInline shape) instead of
//     the template shape.
const SUB_FREQS = new Set(['DAILY', 'WEEKLY']);

export default function TemplateItemFormModal({
  title, subtitle, namePlaceholder, parentTask = null, isTask = false,
  calendarType = 'AD', initial = null, onClose, onSubmit,
  mode = 'template', members = [], parentDueDate = null, periodStart = null, periodEnd = null,
}) {
  const isLive = mode === 'live';
  const editing = !!initial;
  // interval > 1 (on a period-level freq) is how "Custom" is stored.
  const initCustom = !!initial?.frequency
    && (initial.interval_count || 1) > 1
    && !SUB_FREQS.has(initial.frequency);

  // A live draft being re-edited (from the Add Task Subtasks list) carries
  // a plain `due_date`, not a template's due_day_of_period — convert it
  // back to "day N of the task's period, from period start" once so the
  // Day/anchor fields still come up pre-filled.
  const initialLiveDay = (isLive && initial?.due_date && periodStart)
    ? String(Math.max(1, daysBetween(periodStart, initial.due_date) + 1))
    : '';

  const [name, setName] = useState(initial?.title || '');
  const [dueDay, setDueDay] = useState(
    isLive ? initialLiveDay
      : initial?.due_day_of_period != null ? String(initial.due_day_of_period) : '',
  );
  const [dueAnchor, setDueAnchor] = useState(initial?.due_anchor || 'PERIOD_START');
  const [period, setPeriod] = useState(
    isLive && periodStart && periodEnd
      ? { start_date: periodStart, end_date: periodEnd, label: "this task's period" }
      : null,
  );
  const [desc, setDesc] = useState(initial?.description || '');
  const [dueTime, setDueTime] = useState(initial?.due_time ? initial.due_time.slice(0, 5) : '');
  const [frequency, setFrequency] = useState(
    initCustom ? 'CUSTOM' : (initial?.frequency || ''),
  );
  const [customUnit, setCustomUnit] = useState(initCustom ? initial.frequency : 'MONTHLY');
  const [interval, setIntervalN] = useState(initial?.interval_count || 1);
  // A template saved before weekends were closed may still carry Sat(5)/Sun(6);
  // drop them so the form re-saves cleanly (they'd never fire anyway).
  const [dow, setDow] = useState((initial?.days_of_week || []).filter((d) => d < 5));
  const [startDate, setStartDate] = useState(
    isLive ? (initial?.due_date || '') : (initial?.recurrence_start || ''),
  );
  const [endDate, setEndDate] = useState(initial?.recurrence_end || '');
  const [dueDays, setDueDays] = useState(initial?.due_days != null ? String(initial.due_days) : '0');
  const [tags, setTags] = useState(initial?.tags || []);
  const [priority, setPriority] = useState(initial?.priority || 'MEDIUM');
  // Required/Optional — only settable on a Daily task template right now
  // (see schemas.TaskTemplateIn._required_only_daily on the backend).
  const [isRequired, setIsRequired] = useState(initial?.is_required ?? true);
  const [assignedTo, setAssignedTo] = useState(initial?.assigned_to_id || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // The tenant's current (OPEN/IN_PROGRESS) period — used to preview the
  // resolved due date and to translate a calendar pick into a "day N of
  // period". Deliberately `.current()`, not `.active()` ("whichever period's
  // date range covers today"): an admin routinely edits templates for the
  // period that's about to start, or already running, days before/after
  // its exact start/end date — `.active()` would come back empty in that
  // gap and silently drop the whole preview. Live mode already has its
  // period bounds from props (seeded into `period` above), so it skips
  // this network round-trip entirely.
  useEffect(() => {
    if (isLive) return;
    periodAPI.current().then(({ data }) => setPeriod(data)).catch(() => {});
  }, [isLive]);

  const recurring = frequency !== '';
  const isCustom = frequency === 'CUSTOM';

  // day N + anchor  ->  a real date inside the reference period (clamped).
  // This mirrors the backend's own `due_within()` EXACTLY on purpose: a
  // "day N" rule always resolves inside WHICHEVER period it actually
  // generates against, clamped to that period's own last (or first, from
  // a PERIOD_END anchor) day if it doesn't have N days — there is no
  // "day N" that ever produces a date outside the period it's for. So if
  // the reference period is short (e.g. Bhadra 2083 only runs through
  // Sep 16), no day number lets the preview show a later date like Sep 30
  // — that date belongs to a DIFFERENT period (the next one), not this
  // one, and this field can only preview against the one period that's
  // actually loaded (see `period`, fetched above).
  const resolveDate = (n, anchor) => {
    if (!period) return null;
    // A Saturday/Sunday/holiday result is rolled to a working day exactly like
    // the backend's roll_to_working_day: earlier for a PERIOD_END anchor ("N
    // days from close" never slips closer to the close), later otherwise —
    // never out of the period.
    if (!n) return null;
    const N = Math.max(1, Number(n));
    const d = new Date(`${anchor === 'PERIOD_END' ? period.end_date : period.start_date}T00:00:00`);
    d.setDate(d.getDate() + (anchor === 'PERIOD_END' ? -(N - 1) : N - 1));
    const s = new Date(`${period.start_date}T00:00:00`);
    const e = new Date(`${period.end_date}T00:00:00`);
    const clamped = d < s ? period.start_date : d > e ? period.end_date : toLocalIso(d);
    return rollToWorkingDayIso(clamped, {
      lo: period.start_date, hi: period.end_date, preferLater: anchor !== 'PERIOD_END',
    });
  };
  // a picked date  ->  day N (relative to the chosen anchor)
  const dayFromDate = (iso, anchor) => {
    if (!period || !iso) return '';
    const n = anchor === 'PERIOD_END'
      ? daysBetween(iso, period.end_date) + 1
      : daysBetween(period.start_date, iso) + 1;
    return n >= 1 ? String(n) : '1';
  };
  const previewIso = resolveDate(dueDay, dueAnchor);
  // True once the typed Day number is already large enough that it's being
  // clamped to the reference period's own end (or start) — i.e. picking a
  // LATER calendar date can't move the preview any further, because there
  // isn't one inside this period. Surfaced as a hint, not silently hidden,
  // so it reads as "this period is only this long" rather than "the picker
  // is stuck."
  const refPeriodLength = period ? daysBetween(period.start_date, period.end_date) + 1 : 0;
  const clampedToRefPeriod = !!(period && dueDay && Number(dueDay) > refPeriodLength);
  // The real RecurrenceFrequency sent to the backend.
  const effFreq = isCustom ? customUnit : frequency;
  const isDaily = effFreq === 'DAILY';
  const isWeekly = effFreq === 'WEEKLY';
  // Interval ("every N …") is meaningful for a custom cadence and for the
  // sub-period frequencies; the named period-level ones use N = 1.
  const needsInterval = isCustom || isDaily || isWeekly;
  const toggleDow = (i) =>
    setDow((p) => (p.includes(i) ? p.filter((x) => x !== i) : [...p, i].sort()));

  // A sub-task's cap: its parent task's own due date.
  //   - live mode: given directly (`parentDueDate` — the ad-hoc Task's own
  //     chosen due date, already a concrete date), so it applies no matter
  //     what this sub-task's own frequency pick is — there's only ever one
  //     resolved date to compare either way (see `effectiveDueIso` below).
  //   - template mode: resolved from a TaskTemplateOut, and only
  //     computable up front when NEITHER side recurs (see the
  //     component-level comment) — `recurring` here is THIS form's own
  //     frequency pick.
  const parentRecurring = !isLive && !!parentTask?.frequency;
  const parentDueIso = isLive
    ? (parentDueDate || null)
    : (!isTask && parentTask && !parentRecurring && !recurring)
      ? resolveDate(parentTask.due_day_of_period, parentTask.due_anchor || 'PERIOD_START')
      : null;
  const maxAllowedIso = period ? period.end_date : null;
  const effectiveMaxIso = parentDueIso && maxAllowedIso && parentDueIso < maxAllowedIso
    ? parentDueIso : maxAllowedIso;
  // The date THIS sub-task would actually be saved with, even before a day
  // is typed (blank defaults to day 1, same as a Task) — used to validate
  // against `parentDueIso` without waiting for `previewIso`, which is null
  // while `dueDay` is empty. In live mode a recurring pick still only ever
  // saves ONE date — the Start date — since an ad-hoc sub-task doesn't
  // multiply into several rows the way a Close Pack template does.
  const effectiveDueIso = isLive
    ? (recurring ? (startDate || null) : resolveDate(dueDay || 1, dueAnchor))
    : (!recurring ? resolveDate(dueDay || 1, dueAnchor) : null);
  const exceedsParent = !!(parentDueIso && effectiveDueIso && effectiveDueIso > parentDueIso);

  // Every date this form previews/echoes back (resolved due date, parent's
  // due date, the period bounds) — batched through one lookup so a
  // BS-calendar pack's template preview reads in BS, same as the rest of
  // this pack's dates.
  const bsCandidateDates = useMemo(() => {
    const out = [];
    if (effectiveDueIso) out.push(effectiveDueIso);
    if (parentDueIso) out.push(parentDueIso);
    if (previewIso) out.push(previewIso);
    if (period) { out.push(period.start_date); out.push(period.end_date); }
    return out;
  }, [effectiveDueIso, parentDueIso, previewIso, period]);
  const bsLabels = useBsDateLabels(bsCandidateDates, calendarType);
  const fmtD = (iso) => fmtDate(iso, calendarType === 'BS' ? bsLabels : undefined);

  const submit = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) { setError('Give it a name.'); return; }
    if (recurring && !startDate) { setError('Pick a start date for the recurrence.'); return; }
    if (endDate && startDate && endDate < startDate) { setError('End date is before the start date.'); return; }
    if (isWeekly && dow.length === 0) { setError('Pick at least one weekday.'); return; }
    if (exceedsParent) {
      setError(
        `This sub-task would be due ${fmtD(effectiveDueIso)} — after its parent task's due date `
        + `${fmtD(parentDueIso)}. Pick an earlier day (or a different anchor).`,
      );
      return;
    }
    setBusy(true);
    try {
      if (isLive) {
        // TaskSubtaskInline shape — the ad-hoc "Add Task" flow's Subtasks
        // list holds these client-side and submits them together with the
        // Task itself; no API call happens here.
        await onSubmit({
          title: trimmedName,
          description: desc.trim() || null,
          assigned_to_id: assignedTo || null,
          due_date: effectiveDueIso,
          due_date_type: 'WITHIN_PERIOD',
          post_period_due_days: 0,
          due_time: dueTime || null,
          priority,
          tags,
        });
        onClose();
        return;
      }
      // A task and a sub-task both always get a concrete day (defaults to
      // 1) — a sub-task no longer inherits its parent task's due date by
      // leaving it blank; it now always carries its own schedule, only
      // ever bounded by the parent's due date (validated above, and
      // authoritatively re-checked by the backend).
      const dayValue = !recurring ? (dueDay ? Number(dueDay) : 1) : null;
      await onSubmit({
        title: trimmedName,
        due_day_of_period: dayValue,
        due_anchor: dueAnchor,
        due_date_type: 'WITHIN_PERIOD',
        post_period_due_days: 0,
        description: desc.trim() || null,
        due_time: dueTime || null,
        frequency: recurring ? effFreq : null,
        interval_count: needsInterval ? Number(interval) || 1 : 1,
        days_of_week: isWeekly ? dow : null,
        recurrence_start: recurring ? startDate : null,
        recurrence_end: recurring && endDate ? endDate : null,
        due_days: recurring ? (Number(dueDays) || 0) : 0,
        // preserve fields the form doesn't expose (PATCH rewrites every field)
        order: initial?.order ?? 0,
        ...(isTask ? {
          reviewer_required: initial?.reviewer_required ?? false,
          tags,
          priority,
          // Only ever Optional when the frequency actually being submitted
          // is Daily — guards against a stale toggle if the user picked
          // Optional then switched the frequency away from Daily, which
          // the backend would otherwise reject with a validation error.
          is_required: isDaily ? isRequired : true,
        } : {}),
      });
      onClose();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Could not save this.');
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy}>
        {busy ? 'Saving…' : editing ? 'Save changes' : 'Add'}
      </button>
    </>
  );

  return (
    // Same width as the Team Lead's "Add Task" form (AddTaskModal) for both
    // a task template and a sub-task template — there's enough content in
    // either (recurrence, due-date preview, and for a task, priority/tags)
    // that the narrow 480px width cramped it and left fields unevenly
    // spaced. Kept wide for both so the two forms stay visually consistent.
    <Modal title={title} subtitle={subtitle} onClose={onClose} footer={footer}>
      <div className="form-group full">
        <label>Name</label>
        <input
          className="field" type="text" placeholder={namePlaceholder} value={name}
          onChange={(e) => { setName(e.target.value); if (error) setError(''); }}
        />
        {error && <div className="field-error">{error}</div>}
      </div>

      <div className="form-group full">
        <label>Description <span className="hint">— optional</span></label>
        <textarea
          className="field" placeholder="What does this cover?" value={desc}
          onChange={(e) => setDesc(e.target.value)}
        />
      </div>

      {isLive && (
        <div className="form-group full">
          <label>Assignee <span className="hint">— optional</span></label>
          <select className="field" value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)}>
            <option value="">— unassigned —</option>
            {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </div>
      )}

      {/* Frequency + Due Time paired on one row — both are always rendered
          (unlike the recurrence/due-date fields below, which come and go
          with the frequency pick), so pairing them keeps the top of the
          form making full use of the wider modal instead of leaving Due
          Time stranded alone on its own row further down. */}
      <div className="form-row">
        <div className="form-group">
          <label>Frequency <span className="hint">— how often this {isTask ? 'task' : 'sub-task'} recurs</span></label>
          <select className="field" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            {FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label>Due Time <span className="hint">— optional</span></label>
          <input className="field" type="time" value={dueTime} onChange={(e) => setDueTime(e.target.value)} />
        </div>
      </div>
      {isCustom && (
        <div className="form-group full">
          <label>Repeat every <span className="hint">— pick the interval and unit</span></label>
          <div className="form-row">
            <input className="field" type="number" min="1" style={{ maxWidth: 90 }}
                   value={interval} onChange={(e) => setIntervalN(clampInterval(e.target.value))} />
            <select className="field" value={customUnit}
                    onChange={(e) => setCustomUnit(e.target.value)}>
              {CUSTOM_UNITS.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
          </div>
        </div>
      )}
      {!isCustom && (isDaily || isWeekly) && (
        <div className="form-group full">
          <label>
            Every N {isDaily ? 'days' : 'weeks'}
            {isDaily && (
              <span className="hint"> — counted in calendar days from the first occurrence; one that
                falls on a weekend or holiday moves to the next working day</span>
            )}
          </label>
          <input className="field" type="number" min="1" style={{ maxWidth: 150 }} value={interval}
                 onChange={(e) => setIntervalN(clampInterval(e.target.value))} />
        </div>
      )}
      {isTask && isDaily && (
        <div className="form-group full">
          <label className="am-series">
            <input
              type="checkbox"
              checked={!isRequired}
              onChange={(e) => setIsRequired(!e.target.checked)}
            />
            Optional <span className="hint">
              — an uncompleted occurrence is never marked overdue and doesn't count against
              completion compliance; it's still generated and visible for tracking.
            </span>
          </label>
        </div>
      )}
      {isWeekly && (
        <div className="form-group full">
          <label>
            Weekdays <span className="hint">
              — Saturday and Sunday are weekends and are skipped; a weekday that falls on a
              holiday is due the next working day
            </span>
          </label>
          <div className="tif-dow-row">
            {WEEKDAYS.map((lbl, i) => (
              <button type="button" key={lbl}
                      className={`tif-dow-btn${dow.includes(i) ? ' active' : ''}`}
                      onClick={() => toggleDow(i)}>{lbl}</button>
            ))}
          </div>
        </div>
      )}
      {recurring && (
        <div className="form-row">
          <div className="form-group">
            <label>
              First Due Date <span className="hint">
                — {isLive ? 'this sub-task’s due date' : 'first occurrence'}
                {calendarType === 'BS' ? ', pick a Nepali (BS) date' : ''}
              </span>
            </label>
            <DateInput calendarType={calendarType} value={startDate} onChange={setStartDate}
                       max={isLive ? (parentDueIso || undefined) : undefined}
                       placeholder="First due date" />
          </div>
          {!isLive && (
            <div className="form-group">
              <label>End date <span className="hint">— optional</span></label>
              <DateInput calendarType={calendarType} value={endDate} onChange={setEndDate}
                         min={startDate || undefined} placeholder="No end date" />
            </div>
          )}
        </div>
      )}
      {!isLive && recurring && (
        <div className="form-group full">
          <label>
            Due Days <span className="hint">
              — each occurrence is due N days after its own landing date (0 = due the same day
              it lands); it becomes Overdue immediately after that, without waiting for the next
              occurrence. A due date that falls on a Saturday, Sunday or holiday moves to the
              next working day, and never past the end of its own period — near the end of a
              period it becomes that period&apos;s last working day.
            </span>
          </label>
          <input
            className="field" type="number" min="0" style={{ maxWidth: 150 }}
            value={dueDays} onChange={(e) => setDueDays(e.target.value)}
          />
          {startDate && (
            <div className="hint" style={{ marginTop: 4 }}>
              e.g. the {fmtD(startDate)} occurrence would be due{' '}
              {fmtD(rollToWorkingDayIso(toLocalIso(addDaysIso(startDate, dueDays))))}.
            </div>
          )}
        </div>
      )}
      {isLive && recurring && (
        <div className="hint" style={{ marginTop: -8, marginBottom: 12 }}>
          This sub-task doesn&apos;t repeat on its own yet — Frequency just changes how you enter
          the date; only the First Due Date above is saved.
          {parentDueIso && (
            exceedsParent
              ? ` ⚠ After the parent task's due date (${fmtD(parentDueIso)}) — pick an earlier date.`
              : ` Parent task is due ${fmtD(parentDueIso)} — this must land on or before that.`
          )}
        </div>
      )}

      {!recurring && (
        <div className="form-group full">
          <>
              <label>
                Due date <span className="hint">
                  — pick a day within the period; it applies to every period this runs in.
                  Use "from period end", day 1 for the period's last working day
                </span>
              </label>
              <div className="form-row" style={{ alignItems: 'flex-end' }}>
                <div className="form-group" style={{ maxWidth: 110 }}>
                  <span className="hint">Day</span>
                  <input
                    className="field" type="number" min="1"
                    placeholder="e.g. 10" value={dueDay}
                    onChange={(e) => { setDueDay(e.target.value); if (error) setError(''); }}
                  />
                </div>
                <div className="form-group">
                  <span className="hint">counting</span>
                  <select className="field" value={dueAnchor}
                          onChange={(e) => {
                            const a = e.target.value;
                            // keep the same real date when flipping the anchor
                            if (previewIso) setDueDay(dayFromDate(previewIso, a));
                            setDueAnchor(a);
                          }}>
                    {ANCHORS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
                  </select>
                </div>
              </div>
          </>
          {period && (
            <div className="form-group" style={{ marginTop: 4 }}>
              <span className="hint">
                {`or pick any date within ${period.label} to preview against`}
              </span>
              <DateInput
                calendarType={calendarType}
                value={previewIso || ''}
                min={period.start_date}
                max={effectiveMaxIso || undefined}
                onChange={(iso) => setDueDay(dayFromDate(iso, dueAnchor))}
              />
            </div>
          )}
          <div className="hint" style={{ marginTop: 6 }}>
            {!period
              ? 'No active period to preview against.'
              : previewIso
                ? `→ In ${period.label} (${fmtD(period.start_date)} – ${fmtD(period.end_date)}) this is due ${fmtD(previewIso)}.`
                : `Defaults to day 1 (${fmtD(dueAnchor === 'PERIOD_END' ? period.end_date : period.start_date)}).`}
          </div>
          <div className="hint" style={{ marginTop: 2 }}>
            A day that falls on a Saturday, Sunday or holiday moves to the nearest working day —
            the next one when counting from period start, the previous one when counting from period end.
          </div>
          {clampedToRefPeriod && (
            <div className="hint" style={{ marginTop: 2 }}>
              Day {dueDay} is beyond {period.label}'s length ({refPeriodLength} day{refPeriodLength === 1 ? '' : 's'}) —
              it lands on {period.label}'s last day here. A period long enough to have that many days will land on
              its own day {dueDay} instead; a due date in a completely different (later) period isn't something
              "day of period" can express — pick that period's own template/task if you need a fixed date beyond
              this one.
            </div>
          )}
          {parentDueIso && (
            <div className={`hint${exceedsParent ? ' field-error' : ''}`} style={{ marginTop: 2 }}>
              {exceedsParent
                ? `⚠ After the parent task's due date (${fmtD(parentDueIso)}) — pick an earlier day.`
                : `Parent task is due ${fmtD(parentDueIso)} — this sub-task must land on or before that.`}
            </div>
          )}
          {!isTask && parentRecurring && (
            <div className="hint" style={{ marginTop: 2 }}>
              The parent task itself recurs, so its due date moves each occurrence — this
              sub-task's date is automatically capped to it when the plan generates.
            </div>
          )}
        </div>
      )}

      {(isTask || isLive) && (
        <>
          <div className="form-group full">
            <label>Priority <span className="hint">— optional, default Medium</span></label>
            <div className="priority-toggle">
              {PRIORITIES.map((p) => (
                <div
                  key={p.value}
                  className={`priority-opt ${p.cls}${priority === p.value ? ' sel' : ''}`}
                  onClick={() => setPriority(p.value)}
                >
                  {p.label}
                </div>
              ))}
            </div>
          </div>

          <div className="form-group full">
            <label>Tags <span className="hint">— optional</span></label>
            <TagsInput value={tags} onChange={setTags} placeholder="e.g. urgent, reconciliation…" />
          </div>
        </>
      )}
    </Modal>
  );
}
