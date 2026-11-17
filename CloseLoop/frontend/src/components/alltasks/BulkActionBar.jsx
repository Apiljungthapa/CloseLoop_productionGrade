import { useState } from 'react';
import Icon from '../ui/Icon';
import Modal from '../ui/Modal';

// Action bar shown above the task table while one or more rows are ticked
// (Team Lead only — the parent simply doesn't pass the handlers otherwise).
//
//   rows      — the selected row view-models
//   members   — Team Members that can be assigned
//   onAssign(rows, userIds, scope) -> Promise<boolean>   (no userIds = unassign)
//   onReview(rows, decision, note) -> Promise<boolean>   (APPROVE | REJECT)
//   onClear() — drop the selection
//
// Each handler resolves true on success (the dialog closes and the table
// clears its selection) or false after reporting its own error (the dialog
// stays open so the Lead can retry). Approve / Reject are only enabled when
// EVERY selected row is awaiting review — a mixed selection can't be
// reviewed as one batch.
export default function BulkActionBar({ rows, atLimit = false, members = [], onAssign, onReview, onClear }) {
  const [dialog, setDialog] = useState(null); // null | 'assign' | 'unassign' | 'approve' | 'reject'
  const [pickedIds, setPickedIds] = useState(() => new Set());
  const [scope, setScope] = useState('occurrence');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const allInReview = rows.length > 0 && rows.every((r) => r.canReview);
  const seriesRows = rows.filter((r) => r.seriesSize > 1);
  const seriesCount = new Set(
    seriesRows.map((r) => r.raw?.recurring_group_id || `tmpl:${r.raw?.source_template_id}`),
  ).size;
  const assignedCount = rows.filter((r) => (r.assignees || []).length > 0).length;

  const open = (kind) => {
    setPickedIds(new Set());
    setScope('occurrence');
    setNote('');
    setError('');
    setDialog(kind);
  };
  const close = () => { if (!busy) setDialog(null); };
  const toggle = (id) => setPickedIds((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const run = async (fn) => {
    setBusy(true);
    setError('');
    try {
      const ok = await fn();
      if (ok) setDialog(null);
      else setError('That did not go through — see the message at the top of the page.');
    } finally {
      setBusy(false);
    }
  };

  const submitAssign = () => run(() => onAssign(rows, [...pickedIds], seriesRows.length ? scope : 'occurrence'));
  const submitUnassign = () => run(() => onAssign(rows, [], seriesRows.length ? scope : 'occurrence'));
  const submitApprove = () => run(() => onReview(rows, 'APPROVE'));
  const submitReject = () => {
    if (!note.trim()) {
      setError('A reason is required to reject.');
      return;
    }
    run(() => onReview(rows, 'REJECT', note.trim()));
  };

  const scopePicker = seriesRows.length > 0 && (
    <div className="form-group full">
      <label>Recurring tasks</label>
      <label className="bulk-radio">
        <input
          type="radio" name="bulk-scope" checked={scope === 'occurrence'}
          onChange={() => setScope('occurrence')}
        />
        <span>
          Only the {rows.length} selected task{rows.length === 1 ? '' : 's'}
          <small>Other occurrences of a recurring task keep their current assignee.</small>
        </span>
      </label>
      <label className="bulk-radio">
        <input
          type="radio" name="bulk-scope" checked={scope === 'series'}
          onChange={() => setScope('series')}
        />
        <span>
          Every occurrence of {seriesCount} recurring task{seriesCount === 1 ? '' : 's'}
          <small>The whole series is changed once, even if several of its occurrences are selected.</small>
        </span>
      </label>
    </div>
  );

  const footer = (primary) => (
    <>
      <button className="btn" onClick={close} disabled={busy}>Cancel</button>
      {primary}
    </>
  );

  return (
    <>
      <div className="bulk-bar" role="toolbar" aria-label="Bulk task actions">
        <span className="bulk-count">{rows.length} selected</span>
        {atLimit && <span className="bulk-limit">Maximum reached — act on these first</span>}
        <button type="button" className="bulk-btn" onClick={() => open('assign')}>
          <Icon name="layers" size={13} /> Assign
        </button>
        <button type="button" className="bulk-btn" onClick={() => open('unassign')}>
          <Icon name="close" size={13} strokeWidth={2.3} /> Unassign
        </button>
        <span className="bulk-sep" />
        <button
          type="button" className="bulk-btn"
          disabled={!allInReview}
          title={allInReview ? 'Approve all selected' : 'Only tasks awaiting review can be approved'}
          onClick={() => open('approve')}
        >
          Accept
        </button>
        <button
          type="button" className="bulk-btn bulk-btn-danger"
          disabled={!allInReview}
          title={allInReview ? 'Reject all selected' : 'Only tasks awaiting review can be rejected'}
          onClick={() => open('reject')}
        >
          Reject
        </button>
        <button type="button" className="bulk-clear" onClick={onClear}>Clear</button>
      </div>

      {dialog === 'assign' && (
        <Modal
          title={`Assign ${rows.length} task${rows.length === 1 ? '' : 's'}`}
          subtitle="Pick who should work on the selected tasks"
          narrow
          onClose={close}
          footer={footer(
            <button
              className="btn btn-primary" onClick={submitAssign}
              disabled={busy || pickedIds.size === 0}
            >
              {busy ? 'Working…' : 'Assign'}
            </button>,
          )}
        >
          <div className="form-group full">
            <label>Assign to</label>
            {members.length === 0 ? (
              <div className="readonly-field">No Team Members available to assign.</div>
            ) : (
              <div className="bulk-checklist">
                {members.map((m) => (
                  <label key={m.id} className="bulk-check">
                    <input
                      type="checkbox"
                      checked={pickedIds.has(m.id)}
                      onChange={() => toggle(m.id)}
                    />
                    {m.name}
                  </label>
                ))}
              </div>
            )}
          </div>
          {scopePicker}
          {assignedCount > 0 && (
            <div className="readonly-banner" style={{ marginBottom: 0 }}>
              {assignedCount} of the selected tasks already have assignees — they will be replaced.
            </div>
          )}
          {error && <div className="field-error">{error}</div>}
        </Modal>
      )}

      {dialog === 'unassign' && (
        <Modal
          title={`Unassign ${rows.length} task${rows.length === 1 ? '' : 's'}`}
          subtitle="Remove the current assignees from the selected tasks"
          narrow
          onClose={close}
          footer={footer(
            <button className="btn btn-reject" onClick={submitUnassign} disabled={busy}>
              {busy ? 'Working…' : 'Unassign'}
            </button>,
          )}
        >
          <div className="readonly-field">
            Tasks already completed or awaiting review are left as they are.
          </div>
          {scopePicker}
          {error && <div className="field-error">{error}</div>}
        </Modal>
      )}

      {dialog === 'approve' && (
        <Modal
          title={`Accept ${rows.length} task${rows.length === 1 ? '' : 's'}`}
          subtitle="Approve every selected submission"
          narrow
          onClose={close}
          footer={footer(
            <button className="btn btn-primary" onClick={submitApprove} disabled={busy}>
              {busy ? 'Working…' : 'Accept all'}
            </button>,
          )}
        >
          <div className="readonly-field">
            The selected tasks will be marked completed and their assignees notified.
          </div>
          {error && <div className="field-error">{error}</div>}
        </Modal>
      )}

      {dialog === 'reject' && (
        <Modal
          title={`Reject ${rows.length} task${rows.length === 1 ? '' : 's'}`}
          subtitle="Send the selected submissions back with a reason"
          narrow
          onClose={close}
          footer={footer(
            <button className="btn btn-reject" onClick={submitReject} disabled={busy}>
              {busy ? 'Working…' : 'Reject all'}
            </button>,
          )}
        >
          <div className="form-group full">
            <label>Reason <span className="hint">— shared with every assignee</span></label>
            <textarea
              className="field"
              placeholder="Explain what needs to change."
              value={note}
              onChange={(e) => { setNote(e.target.value); if (error) setError(''); }}
            />
          </div>
          {error && <div className="field-error">{error}</div>}
        </Modal>
      )}
    </>
  );
}
