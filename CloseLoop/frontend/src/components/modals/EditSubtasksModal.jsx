import { useMemo, useState } from 'react';
import Modal from '../ui/Modal';
import Icon from '../ui/Icon';
import Badge from '../ui/Badge';
import DateInput from '../ui/DateInput';
import { useAuth } from '../../auth.jsx';
import { subtaskAPI } from '../../apiEntities';
import { getErrorMessage } from '../../api';
import { taskStatusKey, initialsOf, colorFor } from '../../pages/leadDashUtils';

// min(period.end, parent task's own due date) — verbatim the backend rule
// (services/due_date_rules.py: max_allowed_subtask_due_date), so the picker
// never offers a date the server would reject.
const rowMaxDate = (periodEnd, taskDueDate) => {
  if (taskDueDate && periodEnd) return taskDueDate < periodEnd ? taskDueDate : periodEnd;
  return taskDueDate || periodEnd;
};

// Bulk-edit every sub-task on one task in a single place — name, assignee
// and due date. Opened from the task row's ⋮ menu ("Edit subtasks").
// props: task (TaskOut, with .subtasks), members, periodStart, periodEnd,
//        seriesCounts ({ [subtaskId]: occurrenceCount } — >1 means this
//        sub-task recurs alongside sibling occurrences of the same
//        recurring task within the pack; drives a per-row "apply to every
//        occurrence" default so reassigning it doesn't leave the other
//        occurrences on the old assignee), onClose(), onSaved()
export default function EditSubtasksModal({
  task, members = [], periodStart, periodEnd, calendarType = 'AD', seriesCounts = {},
  onClose, onSaved,
}) {
  const { user: me } = useAuth();

  // A Team Lead may also hand a sub-task to themselves, not just a member.
  const assigneeOptions = me.role === 'TEAM_LEAD'
    ? [{ id: me.id, name: `${me.name} (You)` }, ...members]
    : members;

  const original = useMemo(
    () => (task.subtasks || []).map((s) => ({
      id: s.id,
      title: s.title || '',
      assigned_to_id: s.assigned_to_id || '',
      due_date: s.due_date || '',
      status: s.status,
      is_custom: s.is_custom,
    })),
    [task],
  );

  const [rows, setRows] = useState(() => original.map((r) => ({ ...r })));
  // Defaults ON per row with siblings: keying off the ORIGINAL assignee
  // means unassigned-with-siblings rows also start opted in, so picking an
  // assignee for the first time still fans out to every occurrence.
  const [applyAll, setApplyAll] = useState(() => {
    const init = {};
    for (const r of original) {
      if ((seriesCounts[r.id] || 0) > 1) init[r.id] = true;
    }
    return init;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const minDate = periodStart || undefined;

  const patch = (id, key, value) =>
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, [key]: value } : r)));

  const dirtyCount = rows.filter((r, i) => {
    const o = original[i];
    return r.title.trim() !== o.title
      || (r.due_date || '') !== (o.due_date || '')
      || (r.assigned_to_id || '') !== (o.assigned_to_id || '');
  }).length;

  const submit = async () => {
    if (rows.some((r) => !r.title.trim())) {
      setError('Every sub-task needs a name.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      for (let i = 0; i < rows.length; i += 1) {
        const r = rows[i];
        const o = original[i];
        const body = {};
        if (r.title.trim() !== o.title) body.title = r.title.trim();
        if ((r.due_date || '') !== (o.due_date || '')) body.due_date = r.due_date || null;
        const completed = o.status === 'COMPLETED';
        const assigneeChanged = !completed && (r.assigned_to_id || '') !== (o.assigned_to_id || '');
        // Unassigning fans out to the series too, same as picking someone —
        // assign-series accepts 0 ids and clears the assignee on every
        // occurrence.
        const useSeries = assigneeChanged && (seriesCounts[r.id] || 0) > 1 && applyAll[r.id];
        if (assigneeChanged && !useSeries) {
          body.assigned_to_id = r.assigned_to_id || null;
        }
        if (Object.keys(body).length) {
          // eslint-disable-next-line no-await-in-loop
          await subtaskAPI.update(r.id, body);
        }
        if (useSeries) {
          // eslint-disable-next-line no-await-in-loop
          await subtaskAPI.assignSeries(r.id, r.assigned_to_id ? [r.assigned_to_id] : []);
        }
      }
      onSaved();
    } catch (e) {
      setError(getErrorMessage(e, 'Could not save the sub-task changes.'));
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy || dirtyCount === 0}>
        {busy ? 'Saving…' : dirtyCount > 0 ? `Save ${dirtyCount} change${dirtyCount === 1 ? '' : 's'}` : 'Save Changes'}
      </button>
    </>
  );

  return (
    <Modal
      title="Edit Subtasks"
      subtitle={`${task.title} — update each sub-task's name, assignee and due date`}
      onClose={onClose}
      footer={footer}
    >
      {error && <div className="field-error" style={{ marginTop: 0 }}>{error}</div>}

      {rows.length === 0 && <div className="empty-note">This task has no sub-tasks.</div>}

      <div className="subtasks-wrap">
        {rows.map((r, i) => {
          const o = original[i];
          const completed = o.status === 'COMPLETED';
          const sel = assigneeOptions.find((m) => m.id === r.assigned_to_id);
          return (
            <div className="subtask-edit-card" key={r.id}>
              <div className="sec-top">
                <span className="sec-index">{i + 1}</span>
                <Badge status={taskStatusKey({ status: o.status, due_date: o.due_date })} />
                {!o.is_custom && <span className="sec-tmpl">Template</span>}
              </div>

              <div className="form-group full">
                <label>Name</label>
                <input
                  className="field" type="text" value={r.title}
                  onChange={(e) => patch(r.id, 'title', e.target.value)}
                />
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Assignee</label>
                  {completed ? (
                    <div className="empty-note">Completed — reassign is locked.</div>
                  ) : (
                    <div className="sec-assignee-field">
                      {sel && (
                        <span className="sm-avatar" style={{ background: colorFor(sel.id) }}>
                          {initialsOf(sel.name)}
                        </span>
                      )}
                      <select
                        className="field"
                        value={r.assigned_to_id}
                        onChange={(e) => patch(r.id, 'assigned_to_id', e.target.value)}
                      >
                        <option value="">— Unassigned —</option>
                        {assigneeOptions.map((m) => (
                          <option key={m.id} value={m.id}>{m.name}</option>
                        ))}
                      </select>
                    </div>
                  )}
                  {!completed && (seriesCounts[r.id] || 0) > 1 && (
                    <label className="am-series" style={{ marginTop: 6 }}>
                      <input
                        type="checkbox"
                        checked={!!applyAll[r.id]}
                        onChange={() => setApplyAll((prev) => ({ ...prev, [r.id]: !prev[r.id] }))}
                      />
                      Apply to all {seriesCounts[r.id]} occurrences
                    </label>
                  )}
                </div>

                <div className="form-group">
                  <label>Due date</label>
                  <DateInput
                    calendarType={calendarType}
                    value={r.due_date}
                    min={minDate}
                    max={rowMaxDate(periodEnd, task.due_date)}
                    onChange={(v) => patch(r.id, 'due_date', v)}
                  />
                  {r.due_date && (
                    <button type="button" className="link-btn" onClick={() => patch(r.id, 'due_date', '')}>
                      Remove due date
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {(periodStart || task.due_date) && (
        <div className="hint" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <Icon name="calendar" size={12} />
          Due dates must fall
          {periodStart ? ` on/after ${periodStart}` : ''}
          {periodEnd ? ` and on/before the period's end ${periodEnd}` : ''}
          {task.due_date ? ` and the task's ${task.due_date}` : ''}.
        </div>
      )}
    </Modal>
  );
}
