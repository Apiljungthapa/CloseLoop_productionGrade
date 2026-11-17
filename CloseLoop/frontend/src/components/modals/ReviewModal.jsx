import { useState } from 'react';
import Modal from '../ui/Modal';
import { taskAPI, subtaskAPI } from '../../apiEntities';
import { getErrorMessage } from '../../api';

function fmtMinutes(m) {
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// props: item { id, kind: 'TASK'|'SUBTASK', title, assignee, submittedAt,
//               completionNotes, completionMinutes,
//               entityAssignees? — multi-assignee tasks only: each
//               assignee's own done/time/notes (see TaskEntityAssigneeOut),
//               shown instead of the single assignee/time/notes fields
//               above, which stay empty for these }
//        onClose(), onDone()
export default function ReviewModal({ item, onClose, onDone }) {
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (!item) return null;
  const api = item.kind === 'SUBTASK' ? subtaskAPI : taskAPI;

  const run = async (decision) => {
    if (decision === 'REJECT' && !note.trim()) {
      setError('A reason is required to reject this task.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api.review(item.id, { decision, note: note.trim() || undefined });
      onDone(decision);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not record the decision.'));
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-reject" onClick={() => run('REJECT')} disabled={busy}>Reject</button>
      <button className="btn btn-primary" onClick={() => run('APPROVE')} disabled={busy}>
        {busy ? 'Working…' : 'Approve'}
      </button>
    </>
  );

  return (
    <Modal
      title={`Review ${item.kind === 'SUBTASK' ? 'Subtask' : 'Task'}`}
      subtitle="Approve the submission, or reject it with a reason to send it back"
      narrow
      onClose={onClose}
      footer={footer}
    >
      <div className="review-context">
        <div>Task: <b>{item.title}</b></div>
        {item.entityAssignees ? (
          <div>Assigned to: <b>{item.entityAssignees.length} people</b> — everyone has submitted</div>
        ) : (
          <>
            <div>Submitted by: <b>{item.assignee || '—'}</b></div>
            {item.submittedAt && (
              <div>Submitted: <b>{new Date(item.submittedAt).toLocaleString()}</b></div>
            )}
            {item.completionMinutes != null && (
              <div>Time spent: <b>{fmtMinutes(item.completionMinutes)}</b></div>
            )}
          </>
        )}
      </div>

      {item.entityAssignees ? (
        <div className="form-group full">
          <label>Each person's time &amp; notes</label>
          {item.entityAssignees.map((a) => (
            <div className="readonly-field" key={a.id} style={{ marginBottom: 8 }}>
              <div><b>{a.user?.name || 'Member'}</b>{a.completion_time_minutes != null && (
                <> — {fmtMinutes(a.completion_time_minutes)}</>
              )}</div>
              {a.completion_notes && <div>{a.completion_notes}</div>}
            </div>
          ))}
        </div>
      ) : item.completionNotes && (
        <div className="form-group full">
          <label>Assignee's notes</label>
          <div className="readonly-field">{item.completionNotes}</div>
        </div>
      )}

      <div className="form-group full">
        <label>Review Note <span className="hint">— required to reject</span></label>
        <textarea
          className="field"
          placeholder="Feedback for the assignee. If rejecting, explain what needs to change."
          value={note}
          onChange={(e) => { setNote(e.target.value); if (error) setError(''); }}
        />
        {error && <div className="field-error">{error}</div>}
      </div>
    </Modal>
  );
}
