import { useMemo, useState } from 'react';
import Modal from '../ui/Modal';
import Icon from '../ui/Icon';
import Avatar from '../ui/Avatar';
import DateInput from '../ui/DateInput';
import { initialsOf, colorFor, fmtDate } from '../../pages/leadDashUtils';

// A small, ordinary centered Modal (same component/CSS every other dialog
// in the app uses) for picking one person's own deadline. Deliberately NOT
// an inline/anchored popover — this used to be an absolutely-positioned
// DateInput embedded inside the row, and .am-list (the scrollable member
// checklist) clips any descendant that would render past its own box, no
// matter its z-index. A real top-level Modal has no such ancestor to be
// clipped by, stays centered regardless of scroll position or where in the
// list the row sits, and gets responsiveness/Escape-to-close for free.
function AssigneeDueDateModal({
  memberName, value, dueDateBound, calendarType, bsLabels, onSave, onClose,
}) {
  const [date, setDate] = useState(value || '');
  const footer = (
    <>
      <button className="btn" onClick={onClose}>Cancel</button>
      {value && (
        <button className="btn" onClick={() => onSave('')}>Clear deadline</button>
      )}
      <button className="btn btn-primary" onClick={() => onSave(date)}>
        Save deadline
      </button>
    </>
  );
  return (
    <Modal
      title={`Deadline for ${memberName}`}
      subtitle={dueDateBound
        ? `On or before ${fmtDate(dueDateBound, bsLabels)} — the task's own due date`
        : undefined}
      narrow
      onClose={onClose}
      footer={footer}
    >
      <div className="form-group full">
        <label>Due date</label>
        <DateInput
          calendarType={calendarType}
          value={date}
          max={dueDateBound || undefined}
          onChange={setDate}
        />
      </div>
    </Modal>
  );
}

// Pick who a task (or sub-task) is assigned to, from the pack's team.
// A scrollable, searchable checklist — scales past a handful of people,
// unlike an inline chip row.
//
// props:
//   members    — [{ id, name, role? }]  (the team; "(You)" entries fine)
//   current    — string[]  ids to pre-select
//   currentDueDates — { [userId]: 'YYYY-MM-DD' }  existing per-assignee
//                      deadlines, pre-filled when reopening on an
//                      already-assigned task/sub-task
//   title / subtitle
//   seriesCount — >1 shows an "apply to all N occurrences" checkbox
//   showEntity  — show an "Assign whole entity" shortcut in the footer
//   onEntity()  — called when that shortcut is used
//   dueDateBound — the task's/sub-task's own due date; every per-assignee
//                  deadline is capped at this
//   calendarType — 'AD' | 'BS', for the per-assignee date picker
//   onSave(ids, applyToSeries, dueDates)  — dueDates: { [userId]: string | null }
//                  — null means "explicitly clear this person's deadline"
//   onClose()
//   busy        — disables the action buttons
export default function AssigneeManagerModal({
  members = [], current = [], currentDueDates = {}, title = 'Assign from team', subtitle,
  seriesCount = 0, showEntity = false, onEntity, dueDateBound, calendarType = 'AD', bsLabels,
  onSave, onClose, busy = false,
}) {
  const [selected, setSelected] = useState(() => new Set(current));
  const [applyAll, setApplyAll] = useState(seriesCount > 1);
  const [q, setQ] = useState('');
  // Per-assignee deadlines — a shared task/sub-task, staggered across the
  // people on it (e.g. one person's part feeds into the next's). Only
  // meaningful once 2+ people are picked; kept even if a row gets
  // unchecked and re-checked within this same open modal. '' means "no
  // deadline" — that covers both "never had one" and "just cleared one".
  const [dueDates, setDueDates] = useState(() => ({ ...currentDueDates }));
  // Which member's deadline picker modal is open, if any.
  const [pickingFor, setPickingFor] = useState(null);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return members;
    return members.filter((m) => m.name?.toLowerCase().includes(needle));
  }, [members, q]);

  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const setDueDate = (id, value) => setDueDates((prev) => ({ ...prev, [id]: value }));

  const roleLabel = (r) => {
    if (r === 'TEAM_LEAD') return 'Team Lead';
    if (r === 'SUPER_ADMIN') return 'Admin';
    return 'Team Member';
  };

  // Was this task/sub-task already assigned to someone when the modal
  // opened? Unchecking everyone is a legitimate "remove the assignee"
  // action in that case — only a truly no-op save (never assigned, still
  // nothing picked) has nothing to submit.
  const hadAssignees = current.length > 0;
  const multi = selected.size > 1;
  // Only meaningful for people actually being (re)assigned. A plain '' for
  // someone who never had a deadline is left out entirely (nothing to
  // say); someone who HAD one (per currentDueDates) but now reads '' had
  // it explicitly cleared, so that must go through as a real `null` —
  // otherwise the save payload just omits them, the backend never learns
  // the deadline was cleared, and the old date quietly comes back on the
  // next reload (it was never touched, so it was never gone).
  const dueDatesToSave = useMemo(() => {
    if (!multi) return {};
    const out = {};
    for (const id of selected) {
      const val = dueDates[id] || '';
      if (val) out[id] = val;
      else if (currentDueDates[id]) out[id] = null;
    }
    return out;
  }, [multi, selected, dueDates, currentDueDates]);

  const footer = (
    <>
      <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
      {showEntity && onEntity && (
        <button className="btn" onClick={onEntity} disabled={busy}>
          Assign whole entity
        </button>
      )}
      <button
        className="btn btn-primary"
        disabled={busy || (selected.size === 0 && !hadAssignees)}
        onClick={() => onSave([...selected], seriesCount > 1 ? applyAll : false, dueDatesToSave)}
      >
        {busy ? 'Saving…' : selected.size > 1
          ? `Assign ${selected.size} people`
          : selected.size === 1 ? 'Assign 1 person'
            : hadAssignees ? 'Remove assignee' : 'Assign'}
      </button>
    </>
  );

  const pickingMember = pickingFor
    ? members.find((x) => x.id === pickingFor)
    : null;

  return (
    <>
      {/* Modal wires Escape (and backdrop-click) straight to onClose via its
          own window keydown listener — with the due-date picker stacked on
          top (also a Modal, also listening for Escape), pressing Escape
          would fire BOTH and blow away this whole picker along with it.
          Disabling this outer close while the picker's open scopes Escape
          to just the (visually topmost) picker, closing only that. */}
      <Modal
        title={title} subtitle={subtitle}
        onClose={(busy || pickingFor) ? undefined : onClose}
        footer={footer}
      >
        {!members.length ? (
          <div className="empty-note">No team members are scoped to this pack yet.</div>
        ) : (
          <>
            <div className="am-search">
              <Icon name="search" size={14} />
              <input
                type="text"
                placeholder="Search team members…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                autoFocus
              />
            </div>

            <div className="am-toolbar">
              <span>{selected.size} selected</span>
              <span className="am-toolbar-actions">
                <button
                  type="button" className="link-btn"
                  onClick={() => setSelected(new Set(members.map((m) => m.id)))}
                >
                  Select all
                </button>
                {selected.size > 0 && (
                  <button type="button" className="link-btn" onClick={() => setSelected(new Set())}>
                    Clear
                  </button>
                )}
              </span>
            </div>

            <div className="am-list">
              {filtered.length === 0 ? (
                <div className="empty-note" style={{ padding: '10px 4px' }}>No match.</div>
              ) : filtered.map((m) => {
                const isSel = selected.has(m.id);
                const showDue = isSel && multi;
                const due = dueDates[m.id] || '';
                const dueLabel = due ? fmtDate(due, bsLabels) : '';
                return (
                  <label key={m.id} className={`am-row${isSel ? ' sel' : ''}`}>
                    <input type="checkbox" checked={isSel} onChange={() => toggle(m.id)} />
                    <Avatar
                      initials={initialsOf(m.name)}
                      color={colorFor(m.id)}
                      style={{ width: 28, height: 28, fontSize: 10 }}
                    />
                    <div className="am-row-meta">
                      <div className="am-row-name-line">
                        <span className="am-row-name">{m.name}</span>
                        {due && (
                          <span className="am-row-due-chip" title={`Deadline: ${dueLabel}`}>
                            <Icon name="calendar" size={10} /> {dueLabel}
                          </span>
                        )}
                      </div>
                      <div className="am-row-role">{roleLabel(m.role)}</div>
                    </div>
                    {showDue && (
                      <span
                        role="button"
                        tabIndex={0}
                        className={`am-due-toggle${due ? ' set' : ''}`}
                        title={due ? `Deadline: ${dueLabel} — click to change` : 'Give this person their own deadline'}
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setPickingFor(m.id); }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            e.stopPropagation();
                            setPickingFor(m.id);
                          }
                        }}
                      >
                        <Icon name="calendar" size={15} />
                      </span>
                    )}
                  </label>
                );
              })}
            </div>

            {seriesCount > 1 && (
              <label className="am-series">
                <input
                  type="checkbox"
                  checked={applyAll}
                  onChange={() => setApplyAll((v) => !v)}
                />
                Apply to all {seriesCount} occurrences of this recurring task
              </label>
            )}
            {multi && (
              <div className="hint" style={{ marginTop: 8 }}>
                Each person marks their own part done; the task moves to review once all have.
                {' '}Tap the <Icon name="calendar" size={11} /> next to a name to give them their
                own deadline — handy when one person's part depends on another's finishing first.
              </div>
            )}
          </>
        )}
      </Modal>

      {pickingMember && (
        <AssigneeDueDateModal
          memberName={pickingMember.name}
          value={dueDates[pickingMember.id] || ''}
          dueDateBound={dueDateBound}
          calendarType={calendarType}
          bsLabels={bsLabels}
          onSave={(v) => { setDueDate(pickingMember.id, v); setPickingFor(null); }}
          onClose={() => setPickingFor(null)}
        />
      )}
    </>
  );
}
