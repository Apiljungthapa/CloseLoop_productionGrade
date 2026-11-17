import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../auth.jsx';
import Icon from '../ui/Icon';
import Avatar from '../ui/Avatar';
import Badge from '../ui/Badge';
import Toggle from '../ui/Toggle';
import Modal from '../ui/Modal';
import PriorityTag from '../ui/PriorityTag';
import TagsInput from '../ui/TagsInput';
import ReviewModal from '../modals/ReviewModal';
import AssigneeManagerModal from '../modals/AssigneeManagerModal';
import TemplateItemFormModal from '../admin/TemplateItemFormModal';
import { ProgressBar } from '../ui/ProgressBar';
import DateInput from '../ui/DateInput';
import {
  taskAPI, subtaskAPI, ATTACHMENT_MAX_BYTES, ATTACHMENT_ALLOWED_EXT,
} from '../../apiEntities';
import { getErrorMessage } from '../../api';
import {
  taskStatusKey, PRIORITY_TO_KEY, personFromUser,
  initialsOf, colorFor, fmtDate, dueMeta,
} from '../../pages/leadDashUtils';

// min(period.end, parent task's own due date) — same rule as
// services/due_date_rules.py:max_allowed_subtask_due_date, so this inline
// editor's picker never offers a date the backend would reject.
const subtaskMaxDue = (periodEnd, taskDueDate) => {
  if (taskDueDate && periodEnd) return taskDueDate < periodEnd ? taskDueDate : periodEnd;
  return taskDueDate || periodEnd;
};

function fmtBytes(n) {
  if (n == null) return '';
  const kb = n / 1024;
  return kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(kb))} KB`;
}

// Read-only display of a multi-assignee (whole-entity / hand-picked) list.
// Compact horizontal name pills — at most `MAX` shown, the rest collapse
// into a "+N" pill (its title lists them). A done pill is tinted green.
// Per-person time & notes live in the review pop-up, not here.
const ASSIGNEE_PILLS_MAX = 3;

function AssigneeList({ assignees = [], bsLabels }) {
  if (!assignees.length) {
    return <div className="empty-note" style={{ padding: '2px 0 6px' }}>Nobody assigned yet.</div>;
  }
  const shown = assignees.slice(0, ASSIGNEE_PILLS_MAX);
  const hidden = assignees.slice(ASSIGNEE_PILLS_MAX);
  const doneCount = assignees.filter((a) => a.done).length;

  return (
    <div className="assignee-pills-wrap">
      <div className="assignee-pills">
        {shown.map((a) => (
          <span
            key={a.id}
            className={`assignee-pill${a.done ? ' done' : ''}`}
            title={`${a.user?.name || 'Member'} — ${a.done
              ? `done${a.done_at ? ` ${fmtDate(a.done_at, bsLabels)}` : ''}`
              : a.due_date ? `due ${fmtDate(a.due_date, bsLabels)}` : 'in progress'}`}
          >
            <span className="ap-avatar" style={{ background: colorFor(a.user_id) }}>
              {initialsOf(a.user?.name)}
            </span>
            <span className="ap-name">{a.user?.name || 'Member'}</span>
            {!a.done && a.due_date && (
              <span className="ap-due">
                <Icon name="calendar" size={9} /> {fmtDate(a.due_date, bsLabels)}
              </span>
            )}
          </span>
        ))}
        {hidden.length > 0 && (
          <span
            className="assignee-pill assignee-pill-more"
            title={hidden.map((a) => a.user?.name || 'Member').join(', ')}
          >
            +{hidden.length}
          </span>
        )}
      </div>
      <div className="assignee-pills-sub">{doneCount} of {assignees.length} done</div>
    </div>
  );
}

// Time Taken + Notes form, shown as a pop-up overlay — the same shape the
// Team Member sees in SubmitTaskModal. Used whenever a Lead/Admin completes
// their own work (a solo task, their own row on a shared task, a sub-task):
// there's no separate review step to catch a missing value, so both fields
// are required. `onSubmit({ notes, minutes })` does the API call; on
// failure it throws and the message is shown here.
function CompletionFormModal({ title, subtitle, onSubmit, onClose }) {
  const [hours, setHours] = useState('');
  const [minutes, setMinutes] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState('');

  const submit = async () => {
    const total = (Number(hours) || 0) * 60 + (Number(minutes) || 0);
    if (!note.trim()) { setLocalError('Notes are required.'); return; }
    if (total <= 0) { setLocalError('Time taken is required.'); return; }
    setBusy(true);
    setLocalError('');
    try {
      await onSubmit({ notes: note.trim(), minutes: total });
      // parent closes this modal on success — leave `busy` set so the
      // buttons stay disabled during the brief unmount.
    } catch (e) {
      setLocalError(getErrorMessage(e, 'Could not complete this.'));
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy}>
        {busy ? 'Submitting…' : 'Mark complete'}
      </button>
    </>
  );

  return (
    <Modal
      title={title || 'Complete this work'}
      subtitle={subtitle || 'Record your time and notes'}
      narrow
      onClose={busy ? undefined : onClose}
      footer={footer}
    >
      <div className="form-group full">
        <label>Time taken <span className="hint">— required</span></label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="field" type="number" min="0" placeholder="Hours" autoFocus
            value={hours} onChange={(e) => setHours(e.target.value)}
          />
          <input
            className="field" type="number" min="0" max="59" placeholder="Minutes"
            value={minutes} onChange={(e) => setMinutes(e.target.value)}
          />
        </div>
      </div>
      <div className="form-group full">
        <label>Notes <span className="hint">— required</span></label>
        <textarea
          className="field" placeholder="What was done…"
          value={note} onChange={(e) => setNote(e.target.value)}
        />
      </div>
      {localError && <div className="field-error">{localError}</div>}
    </Modal>
  );
}

// props: taskId, members, readOnly, periodStart, periodEnd, onClose(), onChanged()
export default function TaskDetailPanel({
  taskId, members = [], readOnly = false, periodStart, periodEnd,
  calendarType = 'AD', bsLabels, onClose, onChanged,
}) {
  const { user: me } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [commentDraft, setCommentDraft] = useState('');
  const [addingSub, setAddingSub] = useState(false);
  // Which submission the review pop-up (ReviewModal) is open for.
  const [reviewingTask, setReviewingTask] = useState(false);
  const [reviewSubId, setReviewSubId] = useState(null);
  const [subEdit, setSubEdit] = useState(null);   // { id, title } | null
  const [uploading, setUploading] = useState(false);
  const [completingTask, setCompletingTask] = useState(false);
  const [completingEntityRow, setCompletingEntityRow] = useState(false);
  const [completingSubId, setCompletingSubId] = useState(null);
  const [completingSubEntityId, setCompletingSubEntityId] = useState(null);
  // The "Assign from team" picker: null | 'task' | '<subtaskId>'.
  const [assignPicker, setAssignPicker] = useState(null);
  const [assigning, setAssigning] = useState(false);
  // The pack's team, fetched once — the picker scales past a chip row.
  const [team, setTeam] = useState(null);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data: d } = await taskAPI.getDetail(taskId);
      setData(d);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not load this task.'));
    }
  }, [taskId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let cancelled = false;
    taskAPI.team(taskId)
      .then((r) => { if (!cancelled) setTeam(r.data); })
      .catch(() => { if (!cancelled) setTeam(null); });
    return () => { cancelled = true; };
  }, [taskId]);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const mutate = async (fn, failMsg) => {
    setError('');
    try {
      await fn();
      await load();
      onChanged?.();
      return true;
    } catch (e) {
      setError(getErrorMessage(e, failMsg));
      return false;
    }
  };

  if (error && !data) {
    return (
      <div className="detail-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div className="detail-panel" role="dialog" aria-modal="true">
          <div className="detail-head">
            <button className="detail-close" onClick={onClose} aria-label="Close">
              <Icon name="close" size={13} strokeWidth={2.3} />
            </button>
            <div className="detail-title">Task</div>
          </div>
          <div className="detail-body"><div className="reject-banner">{error}</div></div>
        </div>
      </div>
    );
  }
  if (!data) return null;

  const statusKey = taskStatusKey(data);
  const hasSubtasks = (data.subtasks || []).length > 0;
  const taskAssignee = data.member_assigned_to || data.assigned_to;
  const editable = !readOnly;
  const taskMin = periodStart || undefined;
  // The pack's team (entity-scoped, fetched once) with a fallback to the
  // tenant-wide list the parent passes. A Team Lead may also assign work
  // to themselves, so they lead the list.
  const teamMembers = team || members;
  const assigneeOptions = me.role === 'TEAM_LEAD'
    ? [
      { id: me.id, name: `${me.name} (You)`, role: 'TEAM_LEAD' },
      ...teamMembers.filter((m) => m.id !== me.id),
    ]
    : teamMembers;
  // Assigned directly to the viewing Lead — they can complete it
  // themselves (see submit_for_review's self_reviewable check).
  const isMine = !hasSubtasks && !data.assigned_entity_wide
    && (data.member_assigned_to_id === me.id || data.assigned_to_id === me.id);
  const canCompleteTask = isMine
    && data.status !== 'COMPLETED' && data.status !== 'PENDING_REVIEW';

  // ----- mutations -----
  const updateTask = (patch, msg) => mutate(() => taskAPI.update(data.id, patch), msg);
  const updateSub = (sid, patch, msg) => mutate(() => subtaskAPI.update(sid, patch), msg);

  const assignEntity = async () => {
    setAssigning(true);
    const ok = await mutate(
      () => taskAPI.assignEntity(data.id),
      'Could not assign the whole entity.',
    );
    setAssigning(false);
    if (ok) setAssignPicker(null);
  };
  // From the team picker: 0–1 people → a plain single assignment (keeps
  // the normal submit-for-review flow); 2+ → multi-assignee; applyToSeries
  // fans the PEOPLE across every occurrence of a recurring task via
  // assign-series — including 0 people, which clears the assignee on
  // every occurrence instead of just this one. assign-series has no
  // concept of a due date at all (each occurrence has its own due date,
  // so one fixed per-person date wouldn't stay valid across all of them
  // anyway). `dueDates` is therefore always applied as a follow-up
  // assign-multi call scoped to THIS occurrence only — otherwise a date
  // entered here would be silently dropped for every occurrence,
  // including this one, any time "apply to all occurrences" is checked
  // (which it is by default).
  const saveAssignees = async (userIds, applyToSeries = false, dueDates = {}) => {
    const useSeries = applyToSeries && (data.series_count || 0) > 1;
    let runner;
    if (useSeries) {
      runner = async () => {
        await taskAPI.assignSeries(data.id, userIds);
        if (userIds.length > 1 && Object.keys(dueDates).length > 0) {
          await taskAPI.assignMulti(data.id, userIds, dueDates);
        }
      };
    } else if (userIds.length <= 1) {
      runner = () => taskAPI.update(data.id, { member_assigned_to_id: userIds[0] || null });
    } else {
      runner = () => taskAPI.assignMulti(data.id, userIds, dueDates);
    }
    setAssigning(true);
    const ok = await mutate(runner, 'Could not update the assignment.');
    setAssigning(false);
    if (ok) setAssignPicker(null);
  };
  // Time Taken + Notes are required here (see CompletionFormModal) — the
  // backend rejects a self-completion submit that's missing either.
  const completeTask = async ({ notes, minutes }) => {
    await taskAPI.submitForReview(data.id, {
      completion_notes: notes, completion_time_minutes: minutes,
    });
    await load();
    onChanged?.();
    setCompletingTask(false);
  };
  // The Lead is one of several assignees on a multi-assignee task — mark
  // just their own part done, same as any other assignee; the task only
  // completes once everyone's row is done.
  const completeMyEntityRow = async ({ notes, minutes }) => {
    await taskAPI.markDoneEntity(data.id, {
      completion_notes: notes, completion_time_minutes: minutes,
    });
    await load();
    onChanged?.();
    setCompletingEntityRow(false);
  };

  // From the team picker on a sub-task: 0–1 → single assignment; 2+ →
  // multi-assignee; applyToSeries fans it across every occurrence of the
  // parent recurring task's matching sub-task — including 0 people,
  // which clears the assignee on every occurrence. Mirrors saveAssignees
  // at the task level.
  const saveSubAssignees = async (sid, userIds, applyToSeries = false, dueDates = {}) => {
    const st = (data.subtasks || []).find((x) => x.id === sid);
    const useSeries = applyToSeries && (st?.series_count || 0) > 1;
    let runner;
    if (useSeries) {
      runner = async () => {
        await subtaskAPI.assignSeries(sid, userIds);
        // Per-person deadlines are occurrence-specific — see saveAssignees.
        if (userIds.length > 1 && Object.keys(dueDates).length > 0) {
          await subtaskAPI.assignMulti(sid, userIds, dueDates);
        }
      };
    } else if (userIds.length <= 1) {
      runner = () => subtaskAPI.update(sid, { assigned_to_id: userIds[0] || null });
    } else {
      runner = () => subtaskAPI.assignMulti(sid, userIds, dueDates);
    }
    setAssigning(true);
    const ok = await mutate(runner, 'Could not update the sub-task assignment.');
    setAssigning(false);
    if (ok) setAssignPicker(null);
  };
  const assignSubEntity = async (sid) => {
    setAssigning(true);
    const ok = await mutate(
      () => subtaskAPI.assignEntity(sid),
      'Could not assign the whole entity.',
    );
    setAssigning(false);
    if (ok) setAssignPicker(null);
  };
  // The viewing Lead is one of several assignees on a shared sub-task —
  // mark just their own row done (Time Taken + Notes required).
  const completeSubEntityRow = async (sid, { notes, minutes }) => {
    await subtaskAPI.markDoneEntity(sid, {
      completion_notes: notes, completion_time_minutes: minutes,
    });
    await load();
    onChanged?.();
    setCompletingSubEntityId(null);
  };
  // A sub-task assigned to the viewing Lead themselves — same one-step
  // complete as a self-assigned task (see TaskDetailPanel's assignee
  // section / submit_for_review's self_reviewable check on the backend).
  const completeSub = async (sid, { notes, minutes }) => {
    await subtaskAPI.submitForReview(sid, {
      completion_notes: notes, completion_time_minutes: minutes,
    });
    await load();
    onChanged?.();
    setCompletingSubId(null);
  };
  const setSubDue = (sid, value) =>
    updateSub(sid, { due_date: value || null }, 'Could not update the subtask due date.');
  const saveSubTitle = async () => {
    if (!subEdit.title.trim()) { setError('Subtask name cannot be empty.'); return; }
    const ok = await updateSub(subEdit.id, { title: subEdit.title.trim() }, 'Could not rename the subtask.');
    if (ok) setSubEdit(null);
  };
  const deleteSub = (sid) =>
    mutate(() => subtaskAPI.delete(sid), 'Could not delete the subtask.');

  // Build the { ... } shape ReviewModal expects from a task or sub-task —
  // single-assignee items pass submitter + time + notes; multi-assignee
  // items pass the per-person rows instead.
  const reviewItemFor = (it, kind) => ({
    id: it.id,
    kind,
    title: it.title,
    assignee: it.assigned_entity_wide
      ? null
      : (it.submitted_by?.name || it.assigned_to?.name || null),
    submittedAt: it.assigned_entity_wide ? null : it.submitted_at,
    completionNotes: it.assigned_entity_wide ? null : it.completion_notes,
    completionMinutes: it.assigned_entity_wide ? null : it.completion_time_minutes,
    entityAssignees: it.assigned_entity_wide ? (it.entity_assignees || []) : null,
  });

  const afterReview = () => {
    setReviewingTask(false);
    setReviewSubId(null);
    load();
    onChanged?.();
  };

  // Passed straight to TemplateItemFormModal (mode="live") as `onSubmit` —
  // same Frequency/Due-Day form and ≤-parent-due-date validation as the
  // Team Lead's "Add Task" Subtasks section and Admin's Close Pack
  // sub-tasks (see AddTaskModal.jsx / TemplateItemFormModal.jsx). Any
  // rejection (e.g. the backend's own due-date-vs-parent 400) propagates
  // back to the modal, which shows it inline and stays open.
  const createSub = async (draft) => {
    await subtaskAPI.create({
      task_id: data.id,
      title: draft.title,
      description: draft.description,
      assigned_to_id: draft.assigned_to_id,
      due_date: draft.due_date,
      due_time: draft.due_time,
      priority: draft.priority,
      tags: draft.tags,
    });
    await load();
    onChanged?.();
  };

  const uploadFiles = async (fileList) => {
    const files = Array.from(fileList);
    for (const f of files) {
      const ext = '.' + (f.name.split('.').pop() || '').toLowerCase();
      if (!ATTACHMENT_ALLOWED_EXT.includes(ext)) {
        setError(`"${f.name}" — file type ${ext || '(none)'} is not allowed.`);
        return;
      }
      if (f.size > ATTACHMENT_MAX_BYTES) {
        setError(`"${f.name}" is too large (max ${ATTACHMENT_MAX_BYTES / (1024 * 1024)} MB).`);
        return;
      }
    }
    setUploading(true);
    await mutate(async () => {
      for (const f of files) await taskAPI.uploadAttachment(data.id, f);
    }, 'Could not upload the file.');
    setUploading(false);
  };

  const download = async (att) => {
    try {
      const res = await taskAPI.downloadAttachment(data.id, att.id);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url; a.download = att.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not download the file.'));
    }
  };

  const removeAttachment = (att) =>
    mutate(() => taskAPI.deleteAttachment(data.id, att.id), 'Could not remove the file.');

  const postComment = async () => {
    if (!commentDraft.trim()) return;
    const ok = await mutate(() => taskAPI.addComment(data.id, { body: commentDraft.trim() }),
      'Could not post the comment.');
    if (ok) setCommentDraft('');
  };

  return (
    <div className="detail-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="detail-panel" role="dialog" aria-modal="true">
        <div className="detail-head">
          <button className="detail-close" onClick={onClose} aria-label="Close">
            <Icon name="close" size={13} strokeWidth={2.3} />
          </button>
          <div className="detail-title">{data.title}</div>
          <div className="detail-tags">
            <Badge status={statusKey} lateDays={data.late_by_days} />
            <PriorityTag priority={PRIORITY_TO_KEY[data.priority] || 'med'} />
            {data.entity_name && (
              <span className="entity-chip">
                <Icon name="clipboard" size={13} /> {data.entity_name}
              </span>
            )}
          </div>
        </div>

        <div className="detail-body">
          {error && <div className="reject-banner">{error}</div>}
          {data.status === 'REJECTED' && data.rejection_note && (
            <div className="reject-banner"><b>Sent back.</b> {data.rejection_note}</div>
          )}
          {readOnly && (
            <div className="readonly-banner">View only — this pack is not assigned to you.</div>
          )}

          {/* ---------- Description (read-only — use the ⋮ "Edit task" for changes) ---------- */}
          <div className="detail-section">
            <h3>Description</h3>
            <div className="detail-desc">{data.description || 'No description.'}</div>
          </div>

          {/* ---------- Tags — freeform labels, optional ---------- */}
          <div className="detail-section">
            <h3>Tags</h3>
            <TagsInput
              value={data.tags || []}
              onChange={(tags) => updateTask({ tags }, 'Could not update tags.')}
              readOnly={!editable}
            />
          </div>

          {/* ---------- Assignee ---------- */}
          <div className="detail-section">
            <div className="section-head-row">
              <h3 style={{ margin: 0 }}>
                {data.assigned_entity_wide
                  ? `Assignees (${(data.entity_assignees || []).length})`
                  : 'Assignee'}
              </h3>
              {editable && !hasSubtasks && data.status !== 'COMPLETED' && (
                <button className="link-btn" onClick={() => setAssignPicker('task')}>
                  <Icon name="users" size={13} />
                  {' '}
                  {data.assigned_entity_wide || taskAssignee ? 'Manage team' : 'Assign from team'}
                </button>
              )}
            </div>

            {hasSubtasks ? (
              <div className="empty-note" style={{ padding: '2px 0 6px' }}>
                Assigned per sub-task below.
              </div>
            ) : data.assigned_entity_wide ? (
              <>
                <AssigneeList assignees={data.entity_assignees} bsLabels={bsLabels} />
                {(() => {
                  const myRow = (data.entity_assignees || []).find((a) => a.user_id === me.id);
                  if (!myRow || myRow.done || data.status === 'COMPLETED') return null;
                  // Optional task already completed by another assignee.
                  if (data.is_required === false && data.status === 'PENDING_REVIEW') return null;
                  return (
                    <div className="mine-complete-block">
                      <div className="mine-complete-row">
                        <span className="detail-label" style={{ margin: 0 }}>Mark my part done</span>
                        <Toggle
                          variant=""
                          title="Toggle on to log your time & notes and complete your part"
                          onClick={() => setCompletingEntityRow((v) => !v)}
                        />
                      </div>
                    </div>
                  );
                })()}
              </>
            ) : taskAssignee ? (
              <div className="assignee-summary">
                <div className="assignee-chip">
                  <Avatar
                    initials={initialsOf(taskAssignee.name)}
                    color={colorFor(taskAssignee.id)}
                    style={{ width: 30, height: 30, fontSize: 11 }}
                  />
                  <div className="ac-meta">
                    <div className="ac-name">
                      {taskAssignee.name}{taskAssignee.id === me.id ? ' (You)' : ''}
                    </div>
                    <div className="ac-status">
                      {taskAssignee.id === me.id ? 'You (Team Lead)' : 'Team Member'}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="empty-note" style={{ padding: '2px 0 6px' }}>
                Unassigned{editable ? ' — use “Assign from team”.' : '.'}
              </div>
            )}

            {isMine && (
              <div className="mine-complete-block">
                <div className="mine-complete-row">
                  <span className="detail-label" style={{ margin: 0 }}>Mark complete</span>
                  <Toggle
                    variant={data.status === 'COMPLETED' ? 'on'
                      : data.status === 'PENDING_REVIEW' ? 'review' : ''}
                    disabled={!canCompleteTask}
                    title={data.status === 'COMPLETED' ? 'Completed'
                      : data.status === 'PENDING_REVIEW' ? 'Submitted — awaiting review'
                        : 'Toggle on to log your time & notes and complete this task'}
                    onClick={() => { if (canCompleteTask) setCompletingTask((v) => !v); }}
                  />
                </div>
                <div className="empty-note" style={{ marginTop: 4 }}>
                  {data.status === 'COMPLETED' ? 'You marked this task complete.'
                    : data.status === 'PENDING_REVIEW' ? 'Submitted — waiting for review.'
                      : 'Toggle on to add your time taken and notes, then complete.'}
                </div>
              </div>
            )}
          </div>

          {/* ---------- Dates ---------- */}
          <div className="detail-section detail-grid">
            <div>
              <div className="detail-label">Due date</div>
              <div className="dd" style={{ fontSize: 14, fontWeight: 700 }}>
                {data.due_date ? fmtDate(data.due_date, bsLabels) : '—'}
              </div>
              {data.status !== 'COMPLETED' && data.due_date && (
                <div className="dleft">
                  {dueMeta(data.due_date, statusKey, bsLabels)}
                </div>
              )}
            </div>
            {data.status === 'COMPLETED' ? (
              <div>
                <div className="detail-label">Completed</div>
                <div className="dd" style={{ fontSize: 14, fontWeight: 700 }}>
                  {fmtDate(data.updated_at || data.submitted_at, bsLabels)}
                </div>
                {data.completion_time_minutes != null && (
                  <div className="dleft">
                    {Math.floor(data.completion_time_minutes / 60)}h {data.completion_time_minutes % 60}m logged
                  </div>
                )}
              </div>
            ) : <div />}
          </div>

          {/* ---------- Review a member's submission (leaf task) ---------- */}
          {editable && !hasSubtasks && data.status === 'PENDING_REVIEW' && (
            <div className="detail-section">
              <div className="section-head-row">
                <h3 style={{ margin: 0 }}>Review submission</h3>
                <Toggle
                  variant="review"
                  title="Open the review form"
                  onClick={() => setReviewingTask(true)}
                />
              </div>
              <div className="empty-note">
                {data.assigned_entity_wide
                  ? 'Every assignee has marked their part done. Open the review form to see each person’s time & notes, then approve or send it back.'
                  : `Submitted${data.submitted_by?.name ? ` by ${data.submitted_by.name}` : ''}${data.submitted_at ? ` · ${fmtDate(data.submitted_at, bsLabels)}` : ''}. Open the review form to see the time & notes, then approve or send it back.`}
              </div>
            </div>
          )}

          {data.total_subtasks > 0 && (
            <div className="detail-section">
              <div className="detail-progress-row">
                <h3 style={{ margin: 0 }}>Progress</h3>
                <b>{data.completed_subtasks}/{data.total_subtasks}</b>
              </div>
              <ProgressBar pct={Math.round(data.progress_percentage || 0)} />
            </div>
          )}

          {/* ---------- Subtasks ---------- */}
          <div className="detail-section">
            <div className="section-head-row">
              <h3 style={{ margin: 0 }}>Subtasks</h3>
              {editable && (
                <button className="link-btn" onClick={() => setAddingSub(true)}>
                  + Add Subtask
                </button>
              )}
            </div>

            {(data.subtasks || []).length === 0 && (
              <div className="empty-note">No subtasks.</div>
            )}

            {(data.subtasks || []).map((st) => {
              const stStatus = taskStatusKey(st);
              const editingThis = subEdit?.id === st.id;
              return (
                <div className="subtask-card" key={st.id}>
                  {editingThis ? (
                    <div className="form-group" style={{ marginBottom: 8 }}>
                      <label>Subtask Name</label>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <input
                          className="field" type="text" autoFocus value={subEdit.title}
                          onChange={(e) => setSubEdit({ ...subEdit, title: e.target.value })}
                        />
                        <button className="btn btn-primary" onClick={saveSubTitle}>Save</button>
                        <button className="btn" onClick={() => setSubEdit(null)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <div className="section-head-row" style={{ marginBottom: 4 }}>
                      <div className="st-title">{st.title}</div>
                      {editable && (
                        <span style={{ display: 'flex', gap: 10 }}>
                          <button
                            className="link-btn"
                            onClick={() => setSubEdit({ id: st.id, title: st.title })}
                          >
                            Rename
                          </button>
                          {st.is_custom && (
                            <button
                              className="link-btn" style={{ color: 'var(--red)' }}
                              onClick={() => deleteSub(st.id)}
                            >
                              Delete
                            </button>
                          )}
                        </span>
                      )}
                    </div>
                  )}
                  {st.description && <div className="st-desc">{st.description}</div>}

                  {(() => {
                    const myRow = (st.entity_assignees || []).find((a) => a.user_id === me.id);
                    const iAmSingle = st.assigned_to_id === me.id && !st.assigned_entity_wide;
                    const iAmShared = !!st.assigned_entity_wide && !!myRow;
                    return (
                      <>
                        <div className="st-status-row">
                          <Badge status={stStatus} lateDays={st.late_by_days} />
                          {editable && stStatus === 'review' && (
                            <Toggle
                              variant="review"
                              title="Open the review form"
                              onClick={() => setReviewSubId(st.id)}
                            />
                          )}
                          {editable && stStatus !== 'review' && iAmSingle && (
                            <Toggle
                              variant={stStatus === 'completed' ? 'on' : ''}
                              disabled={stStatus === 'completed'}
                              title={stStatus === 'completed' ? 'Completed'
                                : 'Toggle on to log your time & notes and complete this sub-task'}
                              onClick={() => {
                                if (stStatus !== 'completed') {
                                  setCompletingSubId((cur) => (cur === st.id ? null : st.id));
                                }
                              }}
                            />
                          )}
                          {editable && stStatus !== 'review' && iAmShared && (
                            <Toggle
                              variant={myRow.done ? 'on' : ''}
                              disabled={myRow.done || stStatus === 'completed'}
                              title={myRow.done ? 'Your part is done'
                                : 'Toggle on to log your time & notes and complete your part'}
                              onClick={() => {
                                if (!myRow.done && stStatus !== 'completed') {
                                  setCompletingSubEntityId((cur) => (cur === st.id ? null : st.id));
                                }
                              }}
                            />
                          )}
                        </div>

                        {stStatus === 'review' && (st.completion_notes || st.completion_time_minutes != null) && (
                          <div className="assignee-note" style={{ marginLeft: 0 }}>
                            {st.submitted_by?.name && <b>{st.submitted_by.name}</b>}
                            {st.completion_time_minutes != null && (
                              <> · {Math.floor(st.completion_time_minutes / 60)}h {st.completion_time_minutes % 60}m</>
                            )}
                            {st.completion_notes ? ` — ${st.completion_notes}` : ''}
                          </div>
                        )}

                      </>
                    );
                  })()}

                  <div className="st-meta">
                    <div className="st-meta-block">
                      <div className="section-head-row" style={{ marginBottom: 6 }}>
                        <div className="detail-label" style={{ margin: 0 }}>
                          {st.assigned_entity_wide
                            ? `Assignees (${(st.entity_assignees || []).length})`
                            : 'Assignee'}
                        </div>
                        {editable && stStatus !== 'completed' && (
                          <button className="link-btn" onClick={() => setAssignPicker(st.id)}>
                            <Icon name="users" size={13} />{' '}
                            {st.assigned_entity_wide || st.assigned_to ? 'Manage' : 'Assign'}
                          </button>
                        )}
                      </div>
                      {st.assigned_entity_wide ? (
                        <AssigneeList assignees={st.entity_assignees} bsLabels={bsLabels} />
                      ) : st.assigned_to ? (
                        <div className="assignee-chip">
                          <Avatar
                            initials={initialsOf(st.assigned_to.name)}
                            color={colorFor(st.assigned_to.id)}
                            style={{ width: 26, height: 26, fontSize: 9.5 }}
                          />
                          <div className="ac-meta">
                            <div className="ac-name">{st.assigned_to.name}</div>
                            <div className="ac-status">Team Member</div>
                          </div>
                        </div>
                      ) : (
                        <div className="empty-note" style={{ padding: '2px 0 4px' }}>Unassigned</div>
                      )}
                    </div>

                    <div className="st-meta-block st-meta-right">
                      <div className="detail-label">Due date</div>
                      {editable && stStatus !== 'completed' ? (
                        <>
                          <DateInput
                            calendarType={calendarType}
                            value={st.due_date || ''}
                            min={taskMin} max={subtaskMaxDue(periodEnd, data.due_date)}
                            onChange={(v) => setSubDue(st.id, v)}
                          />
                          {st.due_date && (
                            <button className="link-btn" onClick={() => setSubDue(st.id, '')}>
                              Remove
                            </button>
                          )}
                        </>
                      ) : (
                        <div className="dd">{st.due_date ? fmtDate(st.due_date, bsLabels) : '—'}</div>
                      )}
                      {stStatus === 'completed' && (
                        <>
                          <div className="detail-label" style={{ marginTop: 12 }}>Completed</div>
                          <div className="dd">{fmtDate(st.updated_at || st.submitted_at, bsLabels)}</div>
                          {st.completion_time_minutes != null && (
                            <div className="dleft">
                              {Math.floor(st.completion_time_minutes / 60)}h {st.completion_time_minutes % 60}m
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* ---------- Attachments ---------- */}
          <div className="detail-section">
            <div className="section-head-row">
              <h3 style={{ margin: 0 }}>Attachments</h3>
              {editable && (
                <button
                  className="link-btn" disabled={uploading}
                  onClick={() => fileRef.current?.click()}
                >
                  {uploading ? 'Uploading…' : '+ Upload'}
                </button>
              )}
            </div>
            <input
              ref={fileRef} type="file" multiple style={{ display: 'none' }}
              accept={ATTACHMENT_ALLOWED_EXT.join(',')}
              onChange={(e) => { uploadFiles(e.target.files); e.target.value = ''; }}
            />
            {editable && (
              <div
                className="dropzone"
                onClick={() => !uploading && fileRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (!uploading && e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files);
                }}
              >
                <div className="dz-icon"><Icon name="upload" size={22} strokeWidth={1.7} /></div>
                <div>
                  {uploading
                    ? <b>Uploading…</b>
                    : <><b>Click to upload</b> or drag a file here</>}
                </div>
                <div className="muted-sm" style={{ fontSize: 11 }}>
                  Up to {ATTACHMENT_MAX_BYTES / (1024 * 1024)} MB · pdf, office, images, csv, txt, zip
                </div>
              </div>
            )}
            <div className="attach-list">
              {(data.attachments || []).length === 0 && (
                <div className="empty-note">No attachments yet.</div>
              )}
              {(data.attachments || []).map((f) => (
                <div className="attach-item" key={f.id}>
                  <div className="fname" onClick={() => download(f)} style={{ cursor: 'pointer' }}>
                    <div className="ficon">
                      {(f.filename.split('.').pop() || 'FILE').toUpperCase().slice(0, 4)}
                    </div>
                    {f.filename}
                  </div>
                  <div className="fsize">
                    {fmtBytes(f.size_bytes)}
                    {editable && (
                      <span className="attach-remove" onClick={() => removeAttachment(f)}>
                        <Icon name="close" size={13} strokeWidth={2.3} />
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ---------- Comments ---------- */}
          <div className="detail-section">
            <h3>Comments</h3>
            {(data.comments || []).length === 0 && <div className="empty-note">No comments yet.</div>}
            {(data.comments || []).map((c) => {
              const p = personFromUser(c.user) || { name: 'User', initials: '?', color: '#8A90A6' };
              return (
                <div className="comment-item" key={c.id}>
                  <div className="c-avatar" style={{ background: p.color }}>{p.initials}</div>
                  <div className="c-body">
                    <div className="c-name">{p.name}</div>
                    <div className="c-bubble">{c.body}</div>
                    <div className="c-meta">
                      <span className="c-time">{new Date(c.created_at).toLocaleString()}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="detail-footer">
          <input
            type="text" placeholder="Write a comment..."
            value={commentDraft}
            onChange={(e) => setCommentDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && postComment()}
          />
          <button className="btn btn-primary" onClick={postComment}>Send</button>
        </div>
      </div>

      {/* ---- Pop-out forms (rendered outside the slide-over panel) ---- */}
      {completingTask && canCompleteTask && (
        <CompletionFormModal
          title={`Complete "${data.title}"`}
          onSubmit={completeTask}
          onClose={() => setCompletingTask(false)}
        />
      )}
      {completingEntityRow && (
        <CompletionFormModal
          title="Mark your part done"
          subtitle="Record your time and notes for your part of this shared task"
          onSubmit={completeMyEntityRow}
          onClose={() => setCompletingEntityRow(false)}
        />
      )}
      {completingSubId && (() => {
        const st = (data.subtasks || []).find((x) => x.id === completingSubId);
        if (!st) return null;
        return (
          <CompletionFormModal
            title={`Complete "${st.title}"`}
            onSubmit={(vals) => completeSub(st.id, vals)}
            onClose={() => setCompletingSubId(null)}
          />
        );
      })()}
      {completingSubEntityId && (() => {
        const st = (data.subtasks || []).find((x) => x.id === completingSubEntityId);
        if (!st) return null;
        return (
          <CompletionFormModal
            title="Mark your part done"
            subtitle="Record your time and notes for your part of this shared sub-task"
            onSubmit={(vals) => completeSubEntityRow(st.id, vals)}
            onClose={() => setCompletingSubEntityId(null)}
          />
        );
      })()}

      {reviewingTask && (
        <ReviewModal
          item={reviewItemFor(data, 'TASK')}
          onClose={() => setReviewingTask(false)}
          onDone={afterReview}
        />
      )}
      {reviewSubId && (() => {
        const st = (data.subtasks || []).find((x) => x.id === reviewSubId);
        if (!st) return null;
        return (
          <ReviewModal
            item={reviewItemFor(st, 'SUBTASK')}
            onClose={() => setReviewSubId(null)}
            onDone={afterReview}
          />
        );
      })()}

      {assignPicker && (() => {
        const isTask = assignPicker === 'task';
        const st = isTask ? null : (data.subtasks || []).find((x) => x.id === assignPicker);
        if (!isTask && !st) return null;
        const target = isTask ? data : st;
        const current = target.assigned_entity_wide
          ? (target.entity_assignees || []).map((a) => a.user_id)
          : isTask
            ? (target.member_assigned_to_id ? [target.member_assigned_to_id] : [])
            : (target.assigned_to_id ? [target.assigned_to_id] : []);
        // Existing per-assignee deadlines, so reopening this picker on an
        // already-assigned shared task/sub-task shows what's already set.
        const currentDueDates = target.assigned_entity_wide
          ? Object.fromEntries(
            (target.entity_assignees || [])
              .filter((a) => a.due_date)
              .map((a) => [a.user_id, a.due_date]),
          )
          : {};
        return (
          <AssigneeManagerModal
            members={assigneeOptions}
            current={current}
            currentDueDates={currentDueDates}
            dueDateBound={target.due_date || undefined}
            calendarType={calendarType}
            bsLabels={bsLabels}
            title={isTask ? `Assign "${data.title}"` : `Assign "${st.title}"`}
            subtitle={data.entity_name
              ? `Pick from the ${data.entity_name} team`
              : 'Pick who works on this'}
            seriesCount={isTask ? (data.series_count || 0) : (st.series_count || 0)}
            showEntity={!target.assigned_entity_wide}
            onEntity={() => (isTask ? assignEntity() : assignSubEntity(st.id))}
            onSave={(ids, applyAll, dueDates) => (isTask
              ? saveAssignees(ids, applyAll, dueDates)
              : saveSubAssignees(st.id, ids, applyAll, dueDates))}
            onClose={() => setAssignPicker(null)}
            busy={assigning}
          />
        );
      })()}

      {addingSub && (
        <TemplateItemFormModal
          title="Add Subtask"
          subtitle="Same scheduling form and rules as Admin's Close Pack sub-tasks"
          namePlaceholder="e.g. Match transit payments to bank statement"
          mode="live"
          members={assigneeOptions}
          periodStart={periodStart}
          periodEnd={periodEnd}
          parentDueDate={data.due_date || null}
          calendarType={calendarType}
          onClose={() => setAddingSub(false)}
          onSubmit={createSub}
        />
      )}
    </div>
  );
}
