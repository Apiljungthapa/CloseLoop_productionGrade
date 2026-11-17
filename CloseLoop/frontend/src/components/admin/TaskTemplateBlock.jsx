import Icon from '../ui/Icon';
import { toLocalIso } from '../../pages/leadDashUtils';

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const FREQ_LABEL = {
  MONTHLY: 'Monthly', QUARTERLY: 'Quarterly',
  SEMI_ANNUAL: 'Semi-annual', ANNUAL: 'Annual',
};
// Plural unit for a "custom" cadence (interval_count > 1).
const FREQ_UNIT = {
  MONTHLY: 'months', QUARTERLY: 'quarters',
  SEMI_ANNUAL: 'half-years', ANNUAL: 'years',
};

const fmtShort = (v) => {
  if (!v) return '';
  const d = new Date(`${v}T00:00:00`);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
};

// Resolve a template's "day N (+ anchor)" into a real ISO date inside the
// given period — same rule as backend services.due_date_rules.
// resolve_generated_due_date (which itself wraps services.recurrence.
// due_within). The date always stays inside the period.
export function resolveTemplateDueIso(tmpl, period) {
  if (!period || tmpl.frequency) return null;
  if (tmpl.due_day_of_period == null) return null;
  const N = Math.max(1, Number(tmpl.due_day_of_period) || 1);
  const fromEnd = tmpl.due_anchor === 'PERIOD_END';
  const d = new Date(`${fromEnd ? period.end_date : period.start_date}T00:00:00`);
  d.setDate(d.getDate() + (fromEnd ? -(N - 1) : N - 1));
  const s = new Date(`${period.start_date}T00:00:00`);
  const e = new Date(`${period.end_date}T00:00:00`);
  if (d < s) return period.start_date;
  if (d > e) return period.end_date;
  return toLocalIso(d);
}

const dayPill = (tmpl, period, bsMap) => {
  const f = tmpl.frequency;
  const n = tmpl.interval_count || 1;
  const from = tmpl.recurrence_start
    ? ` from ${(bsMap && bsMap[tmpl.recurrence_start]) || fmtShort(tmpl.recurrence_start)}`
    : '';
  // N-Day Due Date — every recurring cadence except "Once per period":
  // each occurrence is due `due_days` days after it lands, not the day it
  // lands (due_days=0, the default, keeps the old "due same day" pill).
  const dueDays = Number(tmpl.due_days) || 0;
  const dueSuffix = dueDays ? ` · due +${dueDays}d` : '';
  if (f === 'DAILY') return (n > 1 ? `Every ${n} days` : 'Daily') + from + dueSuffix;
  if (f === 'WEEKLY') {
    const days = (tmpl.days_of_week || []).map((i) => DOW[i]).join('/');
    const base = n > 1 ? `Every ${n} weeks` : 'Weekly';
    return (days ? `${base} · ${days}` : base) + from + dueSuffix;
  }
  if (f) {
    const base = n > 1 && FREQ_UNIT[f] ? `Every ${n} ${FREQ_UNIT[f]}` : (FREQ_LABEL[f] || f);
    return base + from + dueSuffix;
  }
  if (tmpl.due_day_of_period == null) return null;
  const iso = resolveTemplateDueIso(tmpl, period);
  if (iso) return `Due ${(bsMap && bsMap[iso]) || fmtShort(iso)}`;
  // no active period to resolve against — fall back to the relative label
  const dd = tmpl.due_day_of_period;
  return tmpl.due_anchor === 'PERIOD_END'
    ? (dd === 1 ? 'due last day' : `due day ${dd} from close`)
    : `due day ${dd}`;
};

// One task template inside a pack's Template Structure — collapsed it's a
// title + a due-day pill; expanded it shows its description and sub-tasks.
// `task` is a TaskTemplateOut.
export default function TaskTemplateBlock({
  task, period, bsMap, expanded, onToggle, onAddSubtask, onEditTask,
  onDeleteTask, onEditSubtask, onDeleteSubtask,
}) {
  const freq = dayPill(task, period, bsMap);
  return (
    <div className="task-template-block">
      <div
        className={`task-template-header${expanded ? ' expanded' : ''}`}
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="task-template-name">{task.title}</div>
        <div className="task-template-right">
          {freq && <span className="due-day-pill">{freq}</span>}
          <span className={`task-template-chevron${expanded ? ' open' : ''}`}>
            <Icon name="chevron-down" size={15} strokeWidth={2.4} />
          </span>
        </div>
      </div>

      {expanded && (
        <div className="task-template-body">
          {task.description && <div className="task-template-desc">{task.description}</div>}
          {task.due_time && <div className="task-template-desc">Due time: {task.due_time.slice(0, 5)}</div>}

          <div className="task-template-actions">
            <button type="button" className="btn btn-outline-blue" onClick={onAddSubtask}>
              + Add sub-task template
            </button>
            {onEditTask && (
              <button type="button" className="btn" onClick={() => onEditTask(task)}>
                <Icon name="edit" size={13} /> Edit task
              </button>
            )}
            <button type="button" className="btn btn-outline-red" onClick={onDeleteTask}>
              <Icon name="trash" size={13} /> Delete task
            </button>
          </div>

          {task.sub_task_templates?.length > 0 && (
            <div className="subtask-template-list">
              {task.sub_task_templates.map((sub) => {
                const subFreq = dayPill(sub, period, bsMap);
                return (
                  <div className="subtask-template-card" key={sub.id}>
                    <div className="subtask-template-top">
                      <div className="subtask-template-name">{sub.title}</div>
                      <div className="subtask-template-right">
                        {subFreq && <span className="due-day-pill">{subFreq}</span>}
                        {onEditSubtask && (
                          <button
                            type="button"
                            className="icon-del-btn"
                            aria-label={`Edit ${sub.title}`}
                            onClick={() => onEditSubtask(sub)}
                          >
                            <Icon name="edit" size={13} />
                          </button>
                        )}
                        <button
                          type="button"
                          className="icon-del-btn"
                          aria-label={`Delete ${sub.title}`}
                          onClick={() => onDeleteSubtask(sub.id)}
                        >
                          <Icon name="trash" size={13} />
                        </button>
                      </div>
                    </div>
                    {sub.description && <div className="subtask-template-desc">{sub.description}</div>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
