import { useState } from 'react';
import Modal from '../ui/Modal';
import TagsInput from '../ui/TagsInput';
import DateInput from '../ui/DateInput';
import { useAuth } from '../../auth.jsx';
import { taskAPI } from '../../apiEntities';
import { getErrorMessage } from '../../api';
import { KEY_TO_PRIORITY, PRIORITY_TO_KEY, initialsOf, colorFor } from '../../pages/leadDashUtils';

const PRIORITIES = [
  { key: 'high', cls: 'p-high', label: 'High' },
  { key: 'med', cls: 'p-med', label: 'Medium' },
  { key: 'low', cls: 'p-low', label: 'Low' },
];

// Avatar chips. Single-select by default; pass `multiple` + an array
// `value` for the "assign to one or more people" picker.
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

// props: task (TaskOut), members, periodStart, periodEnd, calendarType,
//        seriesSize (>1 when this task is one occurrence of a recurring
//        daily/weekly/monthly task within the current pack — offers an
//        "apply to every occurrence" default so the Lead assigns the whole
//        recurring task at once instead of one occurrence at a time),
//        onClose(), onSaved()
export default function EditTaskModal({
  task, members = [], periodStart, periodEnd, calendarType = 'AD', seriesSize = 0,
  onClose, onSaved,
}) {
  const { user: me } = useAuth();
  const hasSubtasks = (task.subtasks || []).length > 0;
  const entityWide = !!task.assigned_entity_wide;
  const completed = task.status === 'COMPLETED';

  // A Team Lead may also assign work to themselves, not just a Team Member.
  const assigneeOptions = me.role === 'TEAM_LEAD'
    ? [{ id: me.id, name: `${me.name} (You)` }, ...members]
    : members;

  const [name, setName] = useState(task.title || '');
  const [details, setDetails] = useState(task.description || '');
  // One or more people. One → single assignment; two or more → multi-assignee.
  const [assignees, setAssignees] = useState(
    task.member_assigned_to_id ? [task.member_assigned_to_id] : [],
  );
  const [due, setDue] = useState(task.due_date || '');
  const [dueTime, setDueTime] = useState(task.due_time ? task.due_time.slice(0, 5) : '');
  const [priority, setPriority] = useState(PRIORITY_TO_KEY[task.priority] || 'med');
  const [tags, setTags] = useState(task.tags || []);
  // Defaults ON: a recurring pack task's assignee should land on every
  // occurrence at once unless the Lead deliberately opts this one out.
  const [applyToSeries, setApplyToSeries] = useState(seriesSize > 1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const minDate = periodStart || undefined;
  // A due date always stays inside the task's own period (the backend
  // re-checks this authoritatively, see services/due_date_rules.py).
  const maxDate = periodEnd || undefined;

  const submit = async () => {
    if (!name.trim()) { setError('Task name is required.'); return; }
    setBusy(true);
    setError('');
    try {
      const patch = {
        title: name.trim(),
        description: details.trim() || null,
        due_date: due || null,
        due_time: dueTime || null,
        priority: KEY_TO_PRIORITY[priority],
        tags,
      };
      const canAssign = !hasSubtasks && !entityWide && !completed;
      // Assigning nobody (unassign) fans out to the series too, same as
      // picking someone — assign-series accepts 0 ids and clears the
      // assignee on every occurrence.
      const useSeries = canAssign && applyToSeries && seriesSize > 1;
      const multi = canAssign && !useSeries && assignees.length > 1;
      if (canAssign && !useSeries && !multi) {
        patch.member_assigned_to_id = assignees[0] || null;
      }
      await taskAPI.update(task.id, patch);
      if (useSeries) {
        await taskAPI.assignSeries(task.id, assignees);
      } else if (multi) {
        await taskAPI.assignMulti(task.id, assignees);
      }
      onSaved();
    } catch (e) {
      setError(getErrorMessage(e, 'Could not save the task.'));
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy}>
        {busy ? 'Saving…' : 'Save Changes'}
      </button>
    </>
  );

  return (
    <Modal
      title="Edit Task"
      subtitle="Update this task's details, assignee, due date and priority"
      onClose={onClose}
      footer={footer}
    >
      {error && <div className="field-error" style={{ marginTop: 0 }}>{error}</div>}

      <div className="form-group full">
        <label>Task Name</label>
        <input
          className="field" type="text" autoFocus
          value={name} onChange={(e) => setName(e.target.value)}
        />
      </div>

      <div className="form-group full">
        <label>Assignee</label>
        {hasSubtasks ? (
          <div className="empty-note">This task has subtasks — assign each subtask from the task panel.</div>
        ) : completed ? (
          <div className="empty-note">Completed — reassign is no longer available.</div>
        ) : entityWide ? (
          <div className="empty-note">Assigned to multiple people — manage that from the task panel.</div>
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
            {seriesSize > 1 && (
              <label className="am-series" style={{ marginTop: 8 }}>
                <input
                  type="checkbox"
                  checked={applyToSeries}
                  onChange={() => setApplyToSeries((v) => !v)}
                />
                Apply this assignee to all {seriesSize} occurrences of this recurring task
              </label>
            )}
          </>
        )}
      </div>

      <div className="form-row">
        <div className="form-group">
          <label>Due Date</label>
          <DateInput
            calendarType={calendarType}
            value={due}
            min={minDate}
            max={maxDate}
            onChange={setDue}
          />
          {due && (
            <button type="button" className="link-btn" onClick={() => setDue('')}>
              Remove due date
            </button>
          )}
          {periodStart && periodEnd && (
            <span className="hint">{`Within ${periodStart} → ${periodEnd}`}</span>
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
        <label>Details <span className="hint">— optional</span></label>
        <textarea
          className="field"
          placeholder="Task instructions or context for the assignee..."
          value={details} onChange={(e) => setDetails(e.target.value)}
        />
      </div>

      <div className="form-group full">
        <label>Tags <span className="hint">— optional</span></label>
        <TagsInput value={tags} onChange={setTags} placeholder="e.g. urgent, reconciliation…" />
      </div>
    </Modal>
  );
}
