import { useState } from 'react';
import Modal from '../ui/Modal';
import { teamAPI, taskAPI, subtaskAPI } from '../../apiEntities';
import { getErrorMessage } from '../../api';

// props: item { id, title, kind } — id is a Task OR SubTask id (the unified
//        /api/team/tasks/{id}/submit endpoint accepts either).
//        onClose(), onDone()
//        required — Time Taken + Notes become mandatory: true whenever the
//          backend completes the work immediately with no separate review
//          step (a Lead's own solo task, or anyone's own row on a shared
//          multi-assignee task). A plain Team-Member-to-real-reviewer
//          submission keeps them optional (default).
//        entityWide — this is one assignee's own row on a multi-assignee
//          task (whole-entity or a hand-picked multi-assign): posts to
//          mark-done-entity instead of the regular submit endpoint.
export default function SubmitTaskModal({
  item, onClose, onDone, required = false, entityWide = false,
}) {
  const [hours, setHours] = useState('');
  const [minutes, setMinutes] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (!item) return null;

  const submit = async () => {
    const total = (Number(hours) || 0) * 60 + (Number(minutes) || 0);
    if (required) {
      if (!note.trim()) { setError('Notes are required.'); return; }
      if (total <= 0) { setError('Time taken is required.'); return; }
    }
    setBusy(true);
    setError('');
    try {
      if (entityWide) {
        const markDone = item.kind === 'SUBTASK'
          ? subtaskAPI.markDoneEntity
          : taskAPI.markDoneEntity;
        await markDone(item.id, {
          completion_notes: note.trim(),
          completion_time_minutes: total,
        });
      } else {
        await teamAPI.submit(item.id, {
          comment: note.trim() || null,
          time_minutes: total || null,
        });
      }
      onDone();
    } catch (e) {
      setError(getErrorMessage(
        e, entityWide ? 'Could not mark your part done.' : 'Could not submit your work.',
      ));
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy}>
        {busy ? 'Submitting…' : entityWide ? 'Mark My Part Done' : 'Submit for Review'}
      </button>
    </>
  );

  return (
    <Modal
      title={entityWide ? 'Mark Your Part Done' : 'Submit for Review'}
      subtitle={entityWide
        ? "Record your time and notes for your part of this shared task"
        : 'Send your completed work to your Team Lead'}
      narrow
      onClose={onClose}
      footer={footer}
    >
      <div className="review-context">
        <div>
          {item.kind === 'SUBTASK' ? 'Subtask' : 'Task'}: <b>{item.title}</b>
        </div>
      </div>

      <div className="form-group full">
        <label>Time spent <span className="hint">— {required ? 'required' : 'optional'}</span></label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="field" type="number" min="0" placeholder="Hours"
            value={hours} onChange={(e) => setHours(e.target.value)}
          />
          <input
            className="field" type="number" min="0" max="59" placeholder="Minutes"
            value={minutes} onChange={(e) => setMinutes(e.target.value)}
          />
        </div>
      </div>

      <div className="form-group full">
        <label>
          Note {entityWide ? '' : 'for your Team Lead'}{' '}
          <span className="hint">— {required ? 'required' : 'optional'}</span>
        </label>
        <textarea
          className="field"
          placeholder="What was done…"
          value={note} onChange={(e) => setNote(e.target.value)}
        />
      </div>

      {error && <div className="field-error">{error}</div>}
    </Modal>
  );
}
