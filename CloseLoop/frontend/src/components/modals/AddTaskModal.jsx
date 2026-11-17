import { useMemo, useState } from 'react';
import Modal from '../ui/Modal';
import TagsInput from '../ui/TagsInput';
import DateInput from '../ui/DateInput';
import { workingDayOnOrBefore } from '../ui/workingDays';
import TemplateItemFormModal from '../admin/TemplateItemFormModal';
import { useAuth } from '../../auth.jsx';
import { taskAPI } from '../../apiEntities';
import { getErrorMessage } from '../../api';
import { KEY_TO_PRIORITY, initialsOf, colorFor, fmtDate, useBsDateLabels } from '../../pages/leadDashUtils';

const ASSIGN_MODES = [
  { key: 'individual', label: 'Specific people' },
  { key: 'entity', label: 'Whole Entity' },
];

const PRIORITIES = [
  { key: 'high', cls: 'p-high', label: 'High' },
  { key: 'med', cls: 'p-med', label: 'Medium' },
  { key: 'low', cls: 'p-low', label: 'Low' },
];

const FREQUENCIES = [
  { value: '', label: 'One-off (this period)' },
  { value: 'DAILY', label: 'Daily' },
  { value: 'WEEKLY', label: 'Weekly' },
  { value: 'MONTHLY', label: 'Monthly (once per period)' },
  { value: 'QUARTERLY', label: 'Quarterly' },
  { value: 'SEMI_ANNUAL', label: 'Semi-annual' },
  { value: 'ANNUAL', label: 'Annual' },
];
// Saturday and Sunday are weekends for every tenant — the backend never
// schedules a Daily/Weekly occurrence on them, so they're not offered.
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];

// "Every N days/weeks" can never be 0 (or blank, or negative) — clamp right
// in the input so the field never shows an invalid value in the first place.
const clampInterval = (raw) => {
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
};

let subtaskKey = 0;

// Member avatar chips. Single-select by default (a sub-task takes one
// owner); pass `multiple` and an array `value` for the task-level
// "assign to one or more people" picker. Defined at module scope so it
// isn't remounted on every keystroke.
function MemberChips({ members, value, onPick, multiple = false }) {
  if (!members.length) {
    return <div className="empty-note">No team members in this tenant yet.</div>;
  }
  const selected = multiple ? new Set(value || []) : null;
  return (
    <div className="assignee-select">
      {members.map((m) => {
        const isSel = multiple ? selected.has(m.id) : value === m.id;
        return (
          <div
            key={m.id}
            className={`assignee-opt${isSel ? ' sel' : ''}`}
            onClick={() => {
              if (!multiple) {
                onPick(value === m.id ? '' : m.id);
                return;
              }
              const next = new Set(selected);
              if (next.has(m.id)) next.delete(m.id); else next.add(m.id);
              onPick([...next]);
            }}
          >
            <div className="sm-avatar" style={{ background: colorFor(m.id) }}>{initialsOf(m.name)}</div>
            {m.name}
          </div>
        );
      })}
    </div>
  );
}

// props: planId, periodStart, periodEnd, planDueDate, members (TEAM_MEMBER[]),
//        onClose(), onCreated()
export default function AddTaskModal({
  planId, periodStart, periodEnd, planDueDate, calendarType = 'AD',
  members = [], onClose, onCreated,
}) {
  const { user: me } = useAuth();
  const [name, setName] = useState('');
  const [details, setDetails] = useState('');
  // One or more Team Members (or the Lead themselves). One → normal single
  // assignment; two or more → multi-assignee (everyone marks their own
  // part done, task auto-submits once all have).
  const [assignees, setAssignees] = useState([]);
  const [assignMode, setAssignMode] = useState('individual');
  // Default to the plan's/period's last day — pulled back to the working day
  // before when that is a Saturday, Sunday or holiday (those can't be picked,
  // so such a default would be a date the user couldn't choose themselves) —
  // and left blank if that would fall before the period starts.
  const [due, setDue] = useState(() => {
    const day = workingDayOnOrBefore(planDueDate || periodEnd || '');
    return periodStart && day < periodStart ? '' : day;
  });
  const [dueTime, setDueTime] = useState('');
  const [priority, setPriority] = useState('med');
  const [tags, setTags] = useState([]);
  // Each row here is exactly the TaskSubtaskInline-shaped draft
  // TemplateItemFormModal (mode="live") hands back on submit, plus a local
  // `key` for React/edit/remove — see subtaskFormOpen/editingSubtask below.
  const [subtasks, setSubtasks] = useState([]);
  const [subtaskFormOpen, setSubtaskFormOpen] = useState(false);
  const [editingSubtask, setEditingSubtask] = useState(null);
  const [frequency, setFrequency] = useState('');
  const [interval, setIntervalN] = useState(1);
  const [dow, setDow] = useState([]);
  const [dueDays, setDueDays] = useState('0');
  const [recurrenceEnd, setRecurrenceEnd] = useState('');
  // Required/Optional — only settable for a Daily recurring task right now.
  const [isRequired, setIsRequired] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const isDaily = frequency === 'DAILY';
  const isWeekly = frequency === 'WEEKLY';
  // A recurring custom task's "First Due Date" isn't bounded by the period at
  // all (it's the recurrence anchor, not a due date) — only a one-off
  // task's actual Due Date is, and it must stay <= periodEnd
  // (services/due_date_rules.py is the backend's authoritative check; this
  // only bounds the picker + gives an early error).
  const maxDueIso = (!frequency && periodEnd) ? periodEnd : undefined;
  const toggleDow = (i) => setDow((p) => (p.includes(i) ? p.filter((x) => x !== i) : [...p, i].sort()));

  // A Team Lead may also assign work to themselves, not just a Team Member.
  const assigneeOptions = me.role === 'TEAM_LEAD'
    ? [{ id: me.id, name: `${me.name} (You)` }, ...members]
    : members;

  const addSubtask = (draft) => {
    subtaskKey += 1;
    setSubtasks((prev) => [...prev, { key: subtaskKey, ...draft }]);
  };
  const removeSubtask = (key) => setSubtasks((prev) => prev.filter((s) => s.key !== key));
  const patchSubtask = (key, patch) =>
    setSubtasks((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

  const cleanSubtasks = subtasks.filter((s) => s.title.trim());

  // Subtask due-date preview above (the summary row list) — batched through
  // one lookup so it renders BS for a BS-calendar pack, same as the rest of
  // this pack's dates.
  const bsSubtaskDates = useMemo(
    () => subtasks.filter((s) => s.due_date).map((s) => s.due_date),
    [subtasks],
  );
  const bsLabels = useBsDateLabels(bsSubtaskDates, calendarType);

  const submit = async () => {
    if (!name.trim()) { setError('Task name is required.'); return; }
    if (subtasks.length && !cleanSubtasks.length) {
      setError('Give each subtask a name, or remove the empty ones.');
      return;
    }
    // The per-subtask form already checks this against the task's due date
    // AT THE TIME it's added — re-checked here too since the Lead can add a
    // subtask before picking (or after changing) the task's own due date.
    for (const s of cleanSubtasks) {
      if (due && s.due_date && s.due_date > due) {
        setError(`Subtask "${s.title.trim()}" is due after the task — edit it to pick an earlier date.`);
        return;
      }
    }
    if (isWeekly && dow.length === 0) { setError('Pick at least one weekday for a weekly task.'); return; }
    const wholeEntity = assignMode === 'entity' && cleanSubtasks.length === 0;
    // The people picked in "Specific people" mode (ignored when the task
    // has sub-tasks or is whole-entity — assignment happens elsewhere then).
    const picked = (wholeEntity || cleanSubtasks.length) ? [] : assignees;
    setBusy(true);
    setError('');
    try {
      const { data: created } = await taskAPI.create({
        plan_id: planId,
        title: name.trim(),
        description: details.trim() || null,
        // A single pick goes straight onto the task. For a one-off task,
        // 2+ picks are applied via assign-multi just after creation (like
        // whole-entity, below); for a recurring task the backend applies
        // 2+ picks to every occurrence right at creation via member_ids,
        // so no follow-up call is needed there.
        member_assigned_to_id: picked.length === 1 ? picked[0] : null,
        member_ids: (frequency && picked.length > 1) ? picked : null,
        due_date: due || null,
        due_time: dueTime || null,
        due_date_type: 'WITHIN_PERIOD',
        post_period_due_days: 0,
        priority: KEY_TO_PRIORITY[priority],
        tags,
        subtasks: cleanSubtasks.map((s) => ({
          title: s.title.trim(),
          description: s.description || null,
          assigned_to_id: s.assigned_to_id || null,
          due_date: s.due_date || null,
          due_date_type: 'WITHIN_PERIOD',
          post_period_due_days: 0,
          due_time: s.due_time || null,
          priority: s.priority || null,
          tags: s.tags || [],
        })),
        frequency: frequency || null,
        interval_count: (isDaily || isWeekly) ? Number(interval) || 1 : 1,
        days_of_week: isWeekly ? dow : null,
        // Only ever Optional when Daily is actually the frequency being
        // submitted — same guard as TemplateItemFormModal, against a stale
        // toggle surviving a frequency change away from Daily.
        is_required: isDaily ? isRequired : true,
        due_days: frequency ? (Number(dueDays) || 0) : 0,
        recurrence_end: frequency && recurrenceEnd ? recurrenceEnd : null,
      });
      if (!frequency) {
        if (wholeEntity) {
          await taskAPI.assignEntity(created.id);
        } else if (picked.length > 1) {
          await taskAPI.assignMulti(created.id, picked);
        }
      }
      onCreated();
    } catch (e) {
      setError(getErrorMessage(e, 'Could not create the task.'));
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy}>
        {busy ? 'Creating…' : 'Add Task'}
      </button>
    </>
  );

  return (
    <Modal
      title="Add Task"
      subtitle="Create a task in this pack and assign it to one or more team members"
      onClose={onClose}
      footer={footer}
    >
      {error && <div className="field-error" style={{ marginTop: 0 }}>{error}</div>}

      <div className="form-group full">
        <label>Task Name</label>
        <input
          className="field" type="text" autoFocus
          placeholder="e.g. Reconcile intercompany balances"
          value={name} onChange={(e) => setName(e.target.value)}
        />
      </div>

      {cleanSubtasks.length === 0 && (
        <div className="form-group full">
          <label>Assignment <span className="hint">— optional</span></label>
          <div className="priority-toggle" style={{ marginBottom: 10 }}>
            {ASSIGN_MODES.map((m) => (
              <div
                key={m.key}
                className={`priority-opt${assignMode === m.key ? ' sel' : ''}`}
                onClick={() => setAssignMode(m.key)}
              >
                {m.label}
              </div>
            ))}
          </div>
          {assignMode === 'entity' ? (
            <div className="empty-note">
              Every Team Member scoped to this pack's entity will get this task. It moves to
              review once everyone has submitted their part.
            </div>
          ) : (
            <>
              <MemberChips
                members={assigneeOptions}
                value={assignees}
                onPick={setAssignees}
                multiple
              />
              {assignees.length > 1 && (
                <span className="hint">
                  {assignees.length} people — each marks their own part done; the task moves
                  to review once all have.
                </span>
              )}
            </>
          )}
        </div>
      )}

      <div className="form-row">
        <div className="form-group">
          <label>{frequency ? 'First Due Date' : 'Due Date'}</label>
          <DateInput
            calendarType={calendarType}
            value={due}
            onChange={setDue}
            min={!frequency ? (periodStart || undefined) : undefined}
            max={maxDueIso}
          />
          {frequency ? (
            <span className="hint">First occurrence — the recurrence runs forward from here.</span>
          ) : (
            <span className="hint">Within this pack&apos;s running period.</span>
          )}
        </div>
        <div className="form-group">
          <label>Due Time <span className="hint">— optional</span></label>
          <input className="field" type="time" value={dueTime} onChange={(e) => setDueTime(e.target.value)} />
        </div>
      </div>

      <div className="form-group full">
        <label>Priority</label>
        <div className="priority-toggle">
          {PRIORITIES.map((p) => (
            <div
              key={p.key}
              className={`priority-opt ${p.cls}${priority === p.key ? ' sel' : ''}`}
              onClick={() => setPriority(p.key)}
            >
              {p.label}
            </div>
          ))}
        </div>
      </div>

      <div className="form-group full">
        <label>Repeat <span className="hint">— a recurring custom task materializes across periods</span></label>
        <select className="field" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
          {FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
        {(isDaily || isWeekly) && (
          <input className="field" type="number" min="1" style={{ marginTop: 8 }}
                 value={interval} onChange={(e) => setIntervalN(clampInterval(e.target.value))}
                 placeholder={isDaily ? 'every N days' : 'every N weeks'} />
        )}
        {isDaily && (
          <span className="hint" style={{ display: 'block', marginTop: 4 }}>
            Counted in calendar days from the first occurrence — one that falls on a weekend or
            holiday moves to the next working day.
          </span>
        )}
        {isDaily && (
          <label className="am-series" style={{ marginTop: 8 }}>
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
        )}
        {isWeekly && (
          <div className="tif-dow-row" style={{ marginTop: 8 }}>
            {WEEKDAYS.map((lbl, i) => (
              <button type="button" key={lbl}
                      className={`tif-dow-btn${dow.includes(i) ? ' active' : ''}`}
                      onClick={() => toggleDow(i)}>{lbl}</button>
            ))}
          </div>
        )}
        {frequency && (
          <div style={{ marginTop: 10 }}>
            <label>
              Due Days <span className="hint">
                — each occurrence is due N days after its own landing date (0 = due the same
                day). "First Due Date" above is when it FIRST lands, not necessarily when it's
                due. A due date that falls on a Saturday, Sunday or holiday moves to the next
                working day.
              </span>
            </label>
            <input
              className="field" type="number" min="0" style={{ maxWidth: 150 }}
              value={dueDays} onChange={(e) => setDueDays(e.target.value)}
            />
          </div>
        )}
        {frequency && (
          <div style={{ marginTop: 10 }}>
            <label>End date <span className="hint">— optional, stops the recurrence after this date</span></label>
            <DateInput
              calendarType={calendarType}
              value={recurrenceEnd}
              onChange={setRecurrenceEnd}
              min={due || undefined}
              placeholder="No end date"
            />
          </div>
        )}
      </div>

      <div className="form-group full">
        <label>Details <span className="hint">— optional</span></label>
        <textarea
          className="field"
          placeholder="Add task instructions or context for the assignee..."
          value={details} onChange={(e) => setDetails(e.target.value)}
        />
      </div>

      <div className="form-group full">
        <label>Tags <span className="hint">— optional</span></label>
        <TagsInput value={tags} onChange={setTags} placeholder="e.g. urgent, reconciliation…" />
      </div>

      <div className="form-group full">
        <label>
          Subtasks <span className="hint">
            — optional, same form and scheduling rules as Admin's Close Pack sub-tasks
          </span>
        </label>
        {subtasks.length > 0 && (
          <div className="subtasks-wrap">
            {subtasks.map((s) => (
              <div className="subtask-summary-row" key={s.key}>
                <div className="subtask-summary-main">
                  <span className="subtask-summary-title">{s.title || '(untitled)'}</span>
                  {s.due_date && (
                    <span className="preview-due">
                      Due {fmtDate(s.due_date, calendarType === 'BS' ? bsLabels : undefined)}
                    </span>
                  )}
                  <span className="hint">
                    {assigneeOptions.find((m) => m.id === s.assigned_to_id)?.name || 'Unassigned'}
                  </span>
                </div>
                <div className="subtask-summary-actions">
                  <button type="button" className="link-btn" onClick={() => setEditingSubtask(s)}>
                    Edit
                  </button>
                  <button
                    type="button" className="link-btn" style={{ color: 'var(--red)' }}
                    onClick={() => removeSubtask(s.key)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="add-subtask-btn" onClick={() => setSubtaskFormOpen(true)}>+ Add Subtask</div>
        {cleanSubtasks.length > 0 && (
          <span className="hint">
            The task-level assignee is hidden — assign each subtask to a member instead.
          </span>
        )}
      </div>

      {(subtaskFormOpen || editingSubtask) && (
        <TemplateItemFormModal
          title={editingSubtask ? 'Edit Subtask' : 'Add Subtask'}
          subtitle="Same scheduling form and rules as Admin's Close Pack sub-tasks"
          namePlaceholder="e.g. Match transit payments to bank statement"
          mode="live"
          members={assigneeOptions}
          periodStart={periodStart}
          periodEnd={periodEnd}
          parentDueDate={due || null}
          calendarType={calendarType}
          initial={editingSubtask}
          onClose={() => { setSubtaskFormOpen(false); setEditingSubtask(null); }}
          onSubmit={(draft) => {
            if (editingSubtask) patchSubtask(editingSubtask.key, draft);
            else addSubtask(draft);
          }}
        />
      )}
    </Modal>
  );
}
