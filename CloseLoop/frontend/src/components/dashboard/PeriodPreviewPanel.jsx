import { useMemo, useState } from 'react';
import Avatar from '../ui/Avatar';
import Icon from '../ui/Icon';
import { freqLabel, fmtDate, initialsOf, colorFor, useBsDateLabels } from '../../pages/leadDashUtils';

// Same reading order as the real pack tables: nearest deadline first,
// undated last. Tasks that fall inside the previewed period come first by
// their due date, then the "Upcoming" ones (not due this period) by their
// next due date. Array.sort is stable, so ties keep the template's order.
const NO_DATE = '9999-99-99';
const previewTaskDate = (t) => (t.occurs_in_period ? t.due_date : t.next_due_date) || NO_DATE;
const compareTasksByDeadline = (a, b) => (
  Number(!a.occurs_in_period) - Number(!b.occurs_in_period)
  || previewTaskDate(a).localeCompare(previewTaskDate(b))
);
const compareSubtasksByDeadline = (a, b) => (
  (a.due_date || NO_DATE).localeCompare(b.due_date || NO_DATE)
);

// One expected Task row inside a preview pack — mirrors AllTasksTable's row
// shape (title / frequency / due date) but read-only: there's no real Task
// row behind it yet, so no kebab menu, no status, no toggle — just what
// WOULD exist and when it would be due. `bsLabels` is only ever non-empty
// when the owning preview's pack is a BS pack (see PreviewPackCard).
function PreviewTaskRow({ task, bsLabels }) {
  return (
    <div className="preview-task-row">
      <div className="preview-task-top">
        <span className="preview-task-title">{task.title}</span>
        <span className="freq-chip">{freqLabel(task.frequency, task.interval_count)}</span>
        {task.is_custom && (
          <span className="freq-chip" title="A recurring task added by hand, not from a Close Pack template">
            custom
          </span>
        )}
        {task.occurs_in_period ? (
          <span className="preview-due">
            Due {fmtDate(task.due_date, bsLabels)}
            {task.due_days > 0 && task.occurrence_date
              && ` (+${task.due_days}d after landing on ${fmtDate(task.occurrence_date, bsLabels)})`}
          </span>
        ) : (
          <span className="preview-due preview-due-upcoming">
            Upcoming{task.next_due_date ? ` — next due ${fmtDate(task.next_due_date, bsLabels)}` : ''}
          </span>
        )}
      </div>
      {task.subtasks.length > 0 && (
        <div className="preview-subtask-list">
          {[...task.subtasks].sort(compareSubtasksByDeadline).map((s, i) => (
            <div className="preview-subtask-row" key={s.template_id ?? i}>
              <Icon name="layers" size={12} />
              <span className="preview-subtask-title">{s.title}</span>
              <span className="freq-chip freq-chip-sm">{freqLabel(s.frequency, s.interval_count)}</span>
              <span className="preview-due">
                Due {fmtDate(s.due_date, bsLabels)}
                {s.due_days > 0 && s.occurrence_date
                  && ` (+${s.due_days}d after landing on ${fmtDate(s.occurrence_date, bsLabels)})`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// One (Close Pack × Entity) pairing's expected work for the previewed
// period — the "virtual pack card" equivalent of PackCard, since no real
// Plan exists yet to build a real one from. `bsLabels` (from the panel's
// batched lookup below) is only applied when this preview's own pack is BS.
// Collapsed by default, same as a real Pack card only ever showing its
// tasks once opened — each card's expand state is its own, independent of
// every other pack shown alongside it.
function PreviewPackCard({ preview, bsLabels }) {
  // Collapsed by default — tasks stay tucked inside the pack card until
  // its header is clicked to expand.
  const [expanded, setExpanded] = useState(false);
  const labels = preview.calendar_type === 'BS' ? bsLabels : undefined;
  const taskCount = preview.tasks.length;
  const orderedTasks = useMemo(
    () => [...preview.tasks].sort(compareTasksByDeadline),
    [preview.tasks],
  );
  return (
    <div className="preview-pack-card">
      <div
        className={`preview-pack-head${expanded ? ' expanded' : ''}`}
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((e) => !e)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded((v) => !v); }
        }}
      >
        <div className="preview-pack-head-main">
          <Icon name="layers" size={16} />
          <div>
            <div className="preview-pack-title">{preview.pack_title}</div>
            <div className="preview-pack-sub">{preview.entity_name || 'Unscoped'}</div>
          </div>
        </div>
        <div className="preview-pack-head-right">
          <span className="due-day-pill">{taskCount} task{taskCount === 1 ? '' : 's'}</span>
          {preview.owner ? (
            <span className="lead-chip">
              <Avatar
                className="lead-chip-avatar"
                initials={initialsOf(preview.owner.name)}
                style={{ color: colorFor(preview.owner.id) }}
              />
              <span className="lead-chip-name">{preview.owner.name}</span>
            </span>
          ) : (
            <span className="lead-unassigned">Unassigned</span>
          )}
          <span className={`task-template-chevron${expanded ? ' open' : ''}`}>
            <Icon name="chevron-down" size={15} strokeWidth={2.4} />
          </span>
        </div>
      </div>

      {expanded && (
        taskCount === 0 ? (
          <div className="empty-note">No tasks expected from this pack in this period.</div>
        ) : (
          <div className="preview-task-list">
            {orderedTasks.map((t, i) => (
              <PreviewTaskRow key={t.template_id ?? i} task={t} bsLabels={labels} />
            ))}
          </div>
        )
      )}
    </div>
  );
}

// props: previews (PlanPreviewOut[]), periodLabel, loading
export default function PeriodPreviewPanel({ previews, periodLabel, loading }) {
  // Previews can span several packs of different calendar_type — batch only
  // the BS packs' own dates through one lookup, same pattern as
  // MemberDashboard's bsPackDates/bsItemDates.
  const bsDates = useMemo(() => {
    const out = [];
    for (const pv of previews || []) {
      if (pv.calendar_type !== 'BS') continue;
      for (const t of pv.tasks || []) {
        if (t.due_date) out.push(t.due_date);
        if (t.next_due_date) out.push(t.next_due_date);
        for (const s of t.subtasks || []) {
          if (s.due_date) out.push(s.due_date);
        }
      }
    }
    return out;
  }, [previews]);
  const bsLabels = useBsDateLabels(bsDates, 'BS');

  if (loading) {
    return <div className="empty-note">Calculating expected work for {periodLabel || 'this period'}…</div>;
  }
  if (!previews.length) {
    return (
      <div className="empty-note">
        No Close Pack has ever generated work here, so there is nothing to preview for {periodLabel || 'this period'}.
      </div>
    );
  }
  return (
    <div className="preview-panel">
      <div className="preview-panel-note">
        <Icon name="sparkles" size={14} />
        Calculated from your Close Pack templates' current scheduling rules and the recurring tasks
        you've added — nothing here has been saved. This will become real once{' '}
        {periodLabel || 'this period'} is actually generated.
      </div>
      {previews.map((pv) => (
        <PreviewPackCard key={`${pv.pack_id ?? 'adhoc'}-${pv.entity_id}`} preview={pv} bsLabels={bsLabels} />
      ))}
    </div>
  );
}
