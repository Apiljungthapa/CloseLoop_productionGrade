import { useState } from 'react';
import Modal from '../ui/Modal';
import Toggle from '../ui/Toggle';

// Which calendar every period/date/schedule/report generated from this
// pack follows — replaces the old fixed Pack Type (Daily/Weekly/.../
// Annual). Task-level cadence now lives per task (see TemplateItemFormModal).
export const CALENDAR_TYPES = [
  { value: 'AD', label: 'English (Gregorian / AD)' },
  { value: 'BS', label: 'Nepali (Bikram Sambat / BS)' },
];

// Create a new close pack, or edit an existing one's metadata
// (name / calendar type / status / description) — Template Structure is
// edited separately, from the Template Structure panel.
export default function PackTemplateFormModal({ mode = 'create', initial, onClose, onSubmit }) {
  const [name, setName] = useState(initial?.name ?? '');
  const [calendarType, setCalendarType] = useState(initial?.calendar_type ?? 'AD');
  const [active, setActive] = useState(initial?.active ?? true);
  const [description, setDescription] = useState(initial?.description ?? '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const isEdit = mode === 'edit';

  const submit = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError('Give the pack a name.');
      return;
    }
    setBusy(true);
    try {
      await onSubmit({
        name: trimmedName,
        calendar_type: calendarType,
        active,
        description: description.trim() || null,
      });
      onClose();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Could not save this pack.');
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="btn btn-primary" onClick={submit} disabled={busy}>
        {busy ? 'Saving…' : isEdit ? 'Save Changes' : 'Create Pack'}
      </button>
    </>
  );

  return (
    <Modal
      title={isEdit ? 'Edit Pack' : 'New Pack'}
      subtitle={isEdit ? "Update this pack template's details" : 'Create a re-usable close pack template'}
      onClose={onClose}
      footer={footer}
    >
      <div className="form-group full">
        <label>Pack Name</label>
        <input
          className="field"
          type="text"
          placeholder="e.g. Quarterly Tax Pack"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (error) setError('');
          }}
        />
        {error && <div className="field-error">{error}</div>}
      </div>

      <div className="form-row">
        <div className="form-group">
          <label>Calendar <span className="hint">— governs every period, date, task schedule and report in this pack</span></label>
          <select className="field" value={calendarType} onChange={(e) => setCalendarType(e.target.value)}>
            {CALENDAR_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>

        <div className="form-group">
          <label>Status</label>
          <div className="pack-status-toggle-row">
            <Toggle variant={active ? 'on' : ''} onClick={() => setActive((v) => !v)} />
            <span>{active ? 'Active' : 'Inactive'}</span>
          </div>
        </div>
      </div>

      <div className="form-group full">
        <label>Description <span className="hint">— optional</span></label>
        <textarea
          className="field"
          placeholder="What does this pack cover?"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
    </Modal>
  );
}
