// Shared helpers for the Team Lead dashboard + pack workspace.
// These translate the real backend shapes (PlanOut / TaskOut / SubTaskOut /
// TeamWorkloadMemberOut / UserMini) into the vocabulary the ported mockup
// components expect (status keys like `notstarted`, avatar `initials`/`color`).
import { useEffect, useMemo, useState } from 'react';
import { calendarAPI } from '../apiEntities.js';
import { workingDaysBetweenIso } from '../components/ui/workingDays.js';

const AVATAR_COLORS = [
  '#2F55D4', '#1FA971', '#DE9B1F', '#E5484D', '#8B5CF6',
  '#0EA5E9', '#1A2F6B', '#B9770E',
];

export function initialsOf(name = '') {
  const parts = String(name).trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Mirrors the backend's `_resolve_open_period`/`_resolve_current_period`
// order (see routers/dashboards.py): prefer the OPEN/IN_PROGRESS period
// whose date range contains today, else the earliest OPEN/IN_PROGRESS
// period. Used so the frontend can pick the same "current period" the
// scoped dashboard endpoints would pick, and pass it explicitly as
// `period_id` to every call instead of leaving it to be inferred from
// whichever response happens to come back first.
export function resolveCurrentPeriodId(periods = []) {
  const open = periods.filter((p) => p.status === 'OPEN' || p.status === 'IN_PROGRESS');
  if (open.length === 0) return null;
  const todayStr = todayIso();
  const containing = open
    .filter((p) => p.start_date <= todayStr && p.end_date >= todayStr)
    .sort((a, b) => b.start_date.localeCompare(a.start_date));
  if (containing.length > 0) return containing[0].id;
  return [...open].sort((a, b) => a.start_date.localeCompare(b.start_date))[0].id;
}

export function colorFor(key = '') {
  const s = String(key);
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

// UserMini | { id, name, role?, title? } -> { id, name, initials, color, role, title }
export function personFromUser(u) {
  if (!u) return null;
  return {
    id: u.id,
    name: u.name,
    role: u.role || 'Team Member',
    title: u.title || null,
    initials: initialsOf(u.name),
    color: colorFor(u.id || u.name),
  };
}

// A sub-task's own assignee(s) — entity-wide lists everyone still on the
// hook, otherwise the single member it's assigned to (if any).
export function personsForSubtask(s) {
  if (s.assigned_entity_wide) {
    return (s.entity_assignees || []).map((a) => personFromUser(a.user)).filter(Boolean);
  }
  const a = s.assigned_to;
  return a ? [personFromUser(a)] : [];
}

// De-dupe a list of `{id, ...}` people by id, keeping first occurrence.
export function dedupePeople(people) {
  const seen = new Set();
  const out = [];
  for (const p of people) {
    if (!p || !p.id || seen.has(p.id)) continue;
    seen.add(p.id);
    out.push(p);
  }
  return out;
}

// A task's own assignees, falling back to the union of its sub-tasks'
// assignees when the task carries none directly. A task WITH sub-tasks
// never has its own assigned_to/member_assigned_to set — the backend
// routes assignment onto each sub-task instead (see TaskCreate/TaskUpdate.
// member_assigned_to_id in schemas.py) — so without this fallback such a
// task always showed "Unassigned" in row/summary views even though every
// sub-task under it was assigned to someone.
export function taskAssignees(t, subs) {
  let assignees = [];
  if (t.assigned_entity_wide) {
    assignees = (t.entity_assignees || []).map((a) => personFromUser(a.user)).filter(Boolean);
  } else {
    const a = t.member_assigned_to || t.assigned_to;
    if (a) assignees = [personFromUser(a)];
  }
  if (assignees.length === 0 && subs && subs.length) {
    assignees = dedupePeople(subs.flatMap((s) => personsForSubtask(s)));
  }
  return assignees;
}

// ----- Priority: backend LOW/MEDIUM/HIGH -> mockup low/med/high -----
export const PRIORITY_TO_KEY = { LOW: 'low', MEDIUM: 'med', HIGH: 'high' };
export const KEY_TO_PRIORITY = { low: 'LOW', med: 'MEDIUM', high: 'HIGH' };

// One Task -> the row view-model AllTasksTable/RowMenu expect (title, desc,
// assignees, status, the kebab-menu gating flags...). Shared by every
// pack-detail screen that renders a plan's task tree through AllTasksTable
// (Team Lead's own pack workspace and the Super Admin's pack detail page) so
// the kebab (⋮) menu's contents — Edit task, Edit subtasks, Assign to
// specific people / whole entity, apply-to-series, Delete — are identical
// everywhere a task is managed, regardless of whether the task itself was
// generated from a Close Pack template (by an Admin) or added ad-hoc (by a
// Team Lead); that distinction only ever gates the Delete option via
// `isCustom`; it never hides the rest of the menu.
export function toPlanTaskRow(t, meId, periodStart, periodEnd, bsLabels) {
  const key = taskStatusKey(t);
  const subs = t.subtasks || [];
  const done = subs.filter((s) => s.status === 'COMPLETED').length;
  const assignees = taskAssignees(t, subs);
  // Assigned directly to the viewing user themselves — they complete it
  // the same way a Team Member would (submit-for-review), except the
  // backend auto-completes it immediately since they'd just be
  // approving their own work otherwise.
  const isMineSingle = !t.assigned_entity_wide && subs.length === 0
    && (t.member_assigned_to_id === meId || t.assigned_to_id === meId);
  // Or the viewing user is one of several people this task was assigned to
  // (whole-entity or a hand-picked multi-assign) — they mark their OWN
  // part done the same way any other assignee does; the task only
  // completes once every assignee has done that.
  const myEntityRow = t.assigned_entity_wide
    ? (t.entity_assignees || []).find((a) => a.user_id === meId)
    : null;
  const isMineEntityUndone = !!myEntityRow && !myEntityRow.done;
  const upcoming = key === 'upcoming';
  // A "scheduled placeholder": a period-level recurring task (annual /
  // quarterly-in-monthly) shown in a period it doesn't actually run in —
  // locked, its due date pointing at the next real occurrence.
  const scheduled = upcoming && t.due_date && periodStart && periodEnd
    && (t.due_date < periodStart || t.due_date > periodEnd);
  return {
    id: t.id,
    title: t.title,
    isCustom: !!t.is_custom,
    // Optional — only meaningful on a Daily recurring task right now (see
    // taskStatusKey). `!== false` so a plain one-off task (no is_required
    // in its raw shape at all, e.g. a hand-built test fixture) reads as
    // required, matching the backend's own default.
    isRequired: t.is_required !== false,
    isUpcoming: upcoming,
    isScheduled: scheduled,
    sub: t.carried_from_label
      ? `Carried forward from ${t.carried_from_label}`
      : scheduled ? `Scheduled — next due ${fmtDate(t.due_date, bsLabels)}`
        : upcoming ? 'Upcoming period — locked'
          : (t.is_custom ? 'Custom task' : ''),
    desc: t.description || '',
    assignees,
    due: t.due_date ? fmtDate(t.due_date, bsLabels) : '—',
    dueDateRaw: t.due_date || null,
    dmeta: dueMeta(t.due_date, key, bsLabels),
    status: key,
    lateDays: t.late_by_days || 0,
    priority: PRIORITY_TO_KEY[t.priority] || 'med',
    tags: t.tags || [],
    prog: [done, subs.length],
    subtasks: subs.map((s) => ({
      id: s.id,
      name: s.title,
      desc: s.description || '',
      assignees: personsForSubtask(s),
      due: s.due_date ? fmtDate(s.due_date, bsLabels) : '',
      dueDateRaw: s.due_date || null,
      // A sub-task has no optional flag of its own: it inherits its parent's.
      status: taskStatusKey({ ...s, is_required: t.is_required }),
      lateDays: s.late_by_days || 0,
    })),
    // Only a LEAF task (no sub-tasks) can be reviewed directly — a task
    // with sub-tasks must have each sub-task reviewed individually; its
    // own status is derived from theirs via rollup. Without this guard,
    // approving/rejecting here would overwrite the rolled-up status
    // without ever touching the sub-tasks themselves.
    canReview: key === 'review' && !upcoming && subs.length === 0,
    canComplete: !upcoming && key !== 'completed'
      && ((isMineSingle && key !== 'review') || isMineEntityUndone),
    completeViaEntity: isMineEntityUndone,
    // Completed LEAF task (no sub-tasks, single assignee) can be reopened —
    // flips it back to Pending Review so it can be rejected or re-approved.
    // Sub-task / whole-entity completion rolls up from parts, so those
    // aren't reopened by a blanket flip.
    reopenable: key === 'completed' && subs.length === 0 && !t.assigned_entity_wide,
    raw: t,
  };
}

// Every task in a plan -> row view-models, with each recurring series'
// occurrence count folded in (`seriesSize`) so the kebab menu's "apply to
// every occurrence" checkbox appears consistently regardless of who
// created the underlying task.
export function buildPlanTaskRows(tasks, meId, periodStart, periodEnd, bsLabels) {
  // key: recurring_group_id (custom) or tmpl:<source_template_id> (template)
  const seriesKey = (t) => t.recurring_group_id
    || (t.source_template_id ? `tmpl:${t.source_template_id}` : null);
  const counts = {};
  for (const t of tasks) {
    const k = seriesKey(t);
    if (k) counts[k] = (counts[k] || 0) + 1;
  }
  return tasks.map((t) => {
    const row = toPlanTaskRow(t, meId, periodStart, periodEnd, bsLabels);
    const k = seriesKey(t);
    const size = k ? counts[k] : 1;
    const seriesSize = size > 1 ? size : 0;
    return {
      ...row,
      seriesSize,
      // A recurring series note takes priority over the generic
      // "Custom task" / "" sub-label so the viewer can see it at a glance,
      // but a "Scheduled" placeholder keeps its own next-due label.
      sub: seriesSize && !row.raw?.carried_from_label && !row.isScheduled
        ? `Recurring — ${seriesSize} occurrences this period`
        : row.sub,
    };
  });
}

// Per-sub-task occurrence counts across a plan's recurring series, so
// EditSubtasksModal can offer the same "apply to every occurrence" default
// as the task-level assignee picker. Sibling sub-tasks are matched by their
// own template (or by title for a custom recurring task's sub-tasks, which
// have no template row) across every task in the same recurring series —
// mirrors the backend's get_task_detail computation.
export function buildSubtaskSeriesCounts(tasks) {
  const seriesKey = (t) => t.recurring_group_id
    || (t.source_template_id ? `tmpl:${t.source_template_id}` : null);
  const groups = {};
  for (const t of tasks) {
    const k = seriesKey(t);
    if (!k) continue;
    (groups[k] ||= []).push(t);
  }
  const counts = {};
  for (const group of Object.values(groups)) {
    if (group.length <= 1) continue;
    const subCounts = {};
    for (const t of group) {
      for (const s of (t.subtasks || [])) {
        const key = s.source_template_id || `title:${s.title}`;
        subCounts[key] = (subCounts[key] || 0) + 1;
      }
    }
    for (const t of group) {
      for (const s of (t.subtasks || [])) {
        const key = s.source_template_id || `title:${s.title}`;
        if (subCounts[key] > 1) counts[s.id] = subCounts[key];
      }
    }
  }
  return counts;
}

// Statuses in which an optional task past its due date is 'skipped' (backend: SKIPPABLE_STATUSES).
const SKIPPABLE_STATUSES = new Set(['NOT_STARTED', 'ASSIGNED', 'IN_PROGRESS']);

// ----- Status bucket: mirrors backend dashboards._bucket_for_task -----
// Returns one of: notstarted | progress | duetoday | overdue | completed |
// review | rejected | upcoming | skipped (optional task past its date, not started/assigned/in progress)
export function taskStatusKey(task, today = new Date()) {
  const status = task.status;
  if (status === 'UPCOMING') return 'upcoming';
  if (status === 'COMPLETED') return 'completed';
  // A rejected item is its own bucket regardless of due date — it needs
  // the assignee's attention (resubmit) no matter whether it's also
  // overdue, so it must not get folded into "Overdue"/"Due Today" or
  // silently blend into "Not Started".
  if (status === 'REJECTED') return 'rejected';
  if (status === 'PENDING_REVIEW') return 'review';
  const due = task.due_date ? new Date(task.due_date + 'T00:00:00') : null;
  if (due) {
    const d0 = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    // Optional (task.is_required === false) work counts like any other task
    // EXCEPT once its deadline has passed with nobody having started it: then
    // it is 'skipped' — a neutral "no harm done" state, never overdue, in no
    // total. Done / in-review / rejected optional work was returned above and
    // counts normally. Mirrors backend services/optional_tasks.py.
    if (due < d0) return task.is_required === false && SKIPPABLE_STATUSES.has(status) ? 'skipped' : 'overdue';
    if (due.getTime() === d0.getTime()) return 'duetoday';
  }
  if (status === 'IN_PROGRESS') return 'progress';
  return 'notstarted';
}

// Skipped work (optional, past its date, not started/assigned/in progress — see taskStatusKey) is
// listed but never counted: it lives only under the "Skipped" tab. Every other task — optional ones
// that are done, in review or not yet due included — counts like any task, so a tab's badge always
// equals the rows it lists and a completed optional task shows under "Completed".
//   statusKey  — taskStatusKey / memberStatusKey of the item
//   tab        — the selected tab's status ('' = All Tasks)
export function inStatusTab(statusKey, tab) {
  if (tab === 'skipped') return statusKey === 'skipped';
  if (statusKey === 'skipped') return false;
  return !tab || statusKey === tab;
}

// A raw task / sub-task (a sub-task must carry its parent's is_required) that is skipped today.
export const isSkippedItem = (item, today = new Date()) => taskStatusKey(item, today) === 'skipped';

// Team-Member view — same buckets as taskStatusKey; kept as its own export
// (rather than inlining taskStatusKey everywhere) so member-only call sites
// stay self-documenting and free to diverge again later if a member-specific
// bucket is ever needed.
export function memberStatusKey(item, today = new Date()) {
  return taskStatusKey(item, today);
}

// ----- A member's own work inside ONE pack -----
// The single definition behind both the pack card on the member dashboard and the
// pack screen (/my-packs/:id), so the two can never show different numbers for the
// same pack. `tasks` is the pack's task tree (GET /team/packs/:id → tasks).

// Flatten a pack's task tree into the member's own work. A task that has sub-tasks is
// kept whole: if any of its sub-tasks are mine it becomes one `TASK_WITH_SUBS` entry
// carrying just my sub-tasks, so the member sees the parent task (with its sub-tasks
// nested) rather than loose sub-task rows.
export function memberPackWork(tasks, meId) {
  const out = [];
  const mineOnSub = (s) => s.assigned_to_id === meId
    || (s.assigned_entity_wide
      && (s.entity_assignees || []).some((a) => a.user_id === meId));
  for (const t of tasks || []) {
    // Upcoming (not-yet-actionable) tasks/sub-tasks are kept in the list — they surface
    // under the "Upcoming" status tab — but stay non-actionable and uncounted
    // (see memberPackStats).
    const subs = t.subtasks || [];
    if (subs.length) {
      const mySubs = subs.filter(mineOnSub);
      if (mySubs.length) out.push({ kind: 'TASK_WITH_SUBS', task: t, mySubs });
    } else if (t.assigned_entity_wide) {
      const mine = (t.entity_assignees || []).find((a) => a.user_id === meId);
      if (mine) out.push({ kind: 'TASK', item: t, task: t, entityWide: true, myRow: mine });
    } else if (t.member_assigned_to_id === meId || t.assigned_to_id === meId) {
      out.push({ kind: 'TASK', item: t, task: t });
    }
  }
  return out;
}

// Progress for `memberPackWork` output. Counts are per leaf unit of work (a sub-task,
// an entity-wide row, a plain task) so progress % stays meaningful even though grouped
// tasks render as a single row. Not counted: upcoming (locked, not actionable yet) and
// skipped (optional, past due, not started). `newlyAssigned` = handed to me, not started.
export function memberPackStats(work, meId) {
  let total = 0;
  let completed = 0;
  let review = 0;
  let newlyAssigned = 0;
  const bump = (done, status) => {
    total += 1;
    if (done) completed += 1;
    if (status === 'PENDING_REVIEW') review += 1;
    if (!done && status === 'ASSIGNED') newlyAssigned += 1;
  };
  const myRowOn = (s) => (s.entity_assignees || []).find((a) => a.user_id === meId);
  for (const w of work) {
    if (w.kind === 'TASK_WITH_SUBS') {
      for (const s of w.mySubs) {
        if (s.status === 'UPCOMING') continue; // not actionable yet — excluded from progress
        // Skipped work (optional, past due, not done) is listed but never counted. A
        // sub-task inherits its parent's optional flag; my own tick on a shared one counts as done.
        const mineDone = s.assigned_entity_wide && !!myRowOn(s)?.done;
        if (isSkippedItem({ ...s, is_required: w.task.is_required, status: mineDone ? 'COMPLETED' : s.status })) continue;
        if (s.assigned_entity_wide) {
          const r = myRowOn(s);
          bump(s.status === 'COMPLETED' || !!r?.done, s.status);
        } else {
          bump(s.status === 'COMPLETED', s.status);
        }
      }
    } else if (w.item.status === 'UPCOMING') {
      continue; // not actionable yet — excluded from progress
    } else if (isSkippedItem({ ...w.item, status: w.entityWide && w.myRow.done ? 'COMPLETED' : w.item.status })) {
      continue; // skipped: listed, never counted
    } else if (w.entityWide) {
      bump(!!w.myRow.done, w.item.status);
    } else {
      bump(w.item.status === 'COMPLETED', w.item.status);
    }
  }
  return { total, completed, review, newlyAssigned, pct: total ? Math.round((completed / total) * 100) : 0 };
}

export const STATUS_TABS = [
  { status: '', label: 'All Tasks' },
  { status: 'duetoday', label: 'Due Today' },
  { status: 'overdue', label: 'Overdue' },
  { status: 'skipped', label: 'Skipped' },
  { status: 'notstarted', label: 'Not Started' },
  { status: 'progress', label: 'In Progress' },
  { status: 'completed', label: 'Completed' },
  { status: 'review', label: 'Pending Review' },
  { status: 'rejected', label: 'Rejected' },
  { status: 'upcoming', label: 'Upcoming' },
];

// From GET /dashboards/me/period-work — the signed-in member's own leaf work
// items (a task with sub-tasks contributes its sub-tasks, not itself),
// deduped by id.
//
// `meId` (optional) — when given, a shared/whole-entity item's `due_date`
// is resolved to that user's own TaskEntityAssignee/SubTaskEntityAssignee
// row when the Lead gave them their own deadline, falling back to the
// item's main due date otherwise. Omit it to keep the raw main due date
// (unchanged behavior for any existing caller that doesn't pass it).
export function memberLeafItems(periodWork, meId) {
  const seen = new Set();
  const out = [];
  const add = (it, kind) => {
    if (it && it.id && !seen.has(it.id)) {
      seen.add(it.id);
      const myRow = meId && it.assigned_entity_wide
        ? (it.entity_assignees || []).find((a) => a.user_id === meId)
        : null;
      const due_date = (myRow && myRow.due_date) || it.due_date;
      out.push({ ...it, _kind: kind, due_date });
    }
  };
  for (const t of periodWork?.tasks || []) {
    const subs = t.subtasks || [];
    if (subs.length === 0) add(t, 'TASK');
    // A sub-task has no is_required of its own: it inherits its parent's (optional work stays optional).
    else subs.forEach((s) => add({ ...s, is_required: t.is_required }, 'SUBTASK'));
  }
  for (const s of periodWork?.subtasks || []) add(s, 'SUBTASK');
  return out;
}

// ----- Pack (Plan) status for the dashboard cards -----
// { key, label, tag } — key: unassigned | closed | risk | open
export function packStatus(plan, stats) {
  if (!plan.owner) return { key: 'unassigned', label: 'Unassigned', tag: 'status-unassigned' };
  if (plan.status === 'CLOSED' || (stats && stats.total > 0 && stats.completed === stats.total)) {
    return { key: 'closed', label: 'Completed', tag: 'status-closed' };
  }
  if (stats && stats.overdue > 0) return { key: 'risk', label: 'At Risk', tag: 'status-risk' };
  return { key: 'open', label: 'In Progress', tag: 'status-open' };
}

// Flatten a plan's task tree into the leaf work items that actually carry
// due dates / assignees (a task WITH sub-tasks is a container, not a leaf).
export function leafItems(plans) {
  const out = [];
  for (const p of plans) {
    for (const t of p.tasks || []) {
      const subs = t.subtasks || t.sub_tasks || [];
      if (subs.length) {
        for (const s of subs) out.push({ ...s, kind: 'SUBTASK', plan: p, task: t });
      } else {
        out.push({ ...t, kind: 'TASK', plan: p });
      }
    }
  }
  return out;
}

// `bsLabels` (optional) — { 'YYYY-MM-DD': bsLabel }, from useBsDateLabels
// below. When given and it has an entry for `value`'s date, that BS label
// is returned instead of the AD format — the ONLY thing that changes for a
// BS-mode pack; every caller that doesn't pass it (there are many, and
// none of them need to change) keeps the exact AD formatting it always had.
export function fmtDate(value, bsLabels) {
  if (!value) return '';
  if (bsLabels) {
    const label = bsLabels[String(value).slice(0, 10)];
    if (label) return label;
  }
  const d = new Date(value.length <= 10 ? value + 'T00:00:00' : value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function dueMeta(value, statusKey, bsLabels) {
  if (statusKey === 'completed') return 'Completed';
  if (statusKey === 'review') return 'Submitted for review';
  if (statusKey === 'rejected') return 'Sent back — resubmit';
  if (statusKey === 'skipped') return 'Skipped — optional, no worries';
  if (!value) return 'No due date';
  // Whole-day difference on the DATE part only, in Nepal's "today" (same
  // reference the backend uses), so a datetime value or a viewer in another
  // timezone can't shift the bucket. Weeks run Sunday–Saturday (Nepal).
  const days = dueDayOffset(value);
  if (days === null) return `Due ${fmtDate(value, bsLabels)}`;
  // The relative labels below are calendar-agnostic by nature — only the
  // plain-date fallback needs a BS label, via fmtDate.
  if (days < 0) {
    // Working days only (Saturday, Sunday and configured holidays don't age a
    // task) — due Friday reads "1d overdue" on Monday and plain "Overdue" over
    // the weekend. Mirrors the backend's due_label / working_days_between.
    const overdue = workingDaysBetweenIso(String(value).slice(0, 10), nepalTodayIso());
    return overdue ? `${overdue}d overdue` : 'Overdue';
  }
  if (days === 0) return 'Due today';
  if (days === 1) return 'Due tomorrow';
  const dow = nepalTodayDow(); // 0 = Sunday
  const endOfThisWeek = 6 - dow; // days from today to Saturday
  if (days <= endOfThisWeek) return 'Due this week';
  if (days <= endOfThisWeek + 7) return 'Due next week';
  return `Due ${fmtDate(value, bsLabels)}`;
}

// Today's date in Asia/Kathmandu as { y, m, d } — mirrors backend nepal_today().
function nepalTodayParts() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kathmandu', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const get = (t) => Number(parts.find((p) => p.type === t).value);
  return { y: get('year'), m: get('month'), d: get('day') };
}

function nepalTodayIso() {
  const { y, m, d } = nepalTodayParts();
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function nepalTodayDow() {
  const { y, m, d } = nepalTodayParts();
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

// Whole days from Nepal-today (`t`, from nepalTodayParts) to the date part
// of `value` (negative = past); null if unparseable.
function dueDayOffsetFrom(value, t) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  if (!m) return null;
  const due = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Math.round((due - Date.UTC(t.y, t.m - 1, t.d)) / 86400000);
}

function dueDayOffset(value) {
  return dueDayOffsetFrom(value, nepalTodayParts());
}

// Reading order for a task list — the same Nepal-today as dueMeta, so a row
// labelled "Due today" is always the first one:
//   1. due today, then tomorrow, then further out (soonest first)
//   2. no due date
//   3. deadlines already missed, the most recently missed first
// Rows that land on the same day put the higher `priority` rank (lower
// number) first; anything still tied keeps its incoming order. Returns a new
// array and never touches `items`. Mirrors the backend's plans._task_order.
export function sortByDeadline(items, getDate, getPriority = () => 0) {
  const today = nepalTodayParts(); // once per sort, not once per comparison
  return items
    .map((item, i) => {
      const days = dueDayOffsetFrom(getDate(item), today);
      if (days === null) return { item, i, group: 1, dist: 0, rank: getPriority(item) };
      return days >= 0
        ? { item, i, group: 0, dist: days, rank: getPriority(item) }
        : { item, i, group: 2, dist: -days, rank: getPriority(item) };
    })
    .sort((a, b) => (
      a.group - b.group || a.dist - b.dist || a.rank - b.rank || a.i - b.i
    ))
    .map((x) => x.item);
}

// Which upcoming-week bucket a due date falls in, using the SAME Nepal-today
// and Sunday–Saturday weeks as dueMeta: 'thisweek' = tomorrow through this
// Saturday, 'nextweek' = the following Sunday–Saturday. Today, past dates,
// later dates and undated values return null.
export function dueWeekBucket(value) {
  if (!value) return null;
  const days = dueDayOffset(value);
  if (days === null || days <= 0) return null;
  const endOfThisWeek = 6 - nepalTodayDow();
  if (days <= endOfThisWeek) return 'thisweek';
  if (days <= endOfThisWeek + 7) return 'nextweek';
  return null;
}

// Batches every date in `dates` through one POST /calendar/convert-batch
// call and returns { 'YYYY-MM-DD': bsLabel } once it resolves. For an AD
// pack (calendarType !== 'BS') this is always {} — pass the result straight
// into fmtDate/dueMeta unconditionally; there's nothing to branch on at the
// call site. While a BS pack's batch is still loading it's also {}, so
// dates simply render as AD for one instant and then re-render in BS the
// moment this hook's state resolves — no loading flag to thread through.
export function useBsDateLabels(dates, calendarType) {
  // Deduped + sorted into one string so the effect below depends on a
  // stable primitive — an array of the "same" dates gets a new identity
  // every render, which would refetch forever.
  const key = useMemo(() => {
    const uniq = [...new Set((dates || []).filter(Boolean).map((d) => String(d).slice(0, 10)))];
    uniq.sort();
    return uniq.join(',');
  }, [dates]);

  const [labels, setLabels] = useState({});
  const active = calendarType === 'BS' && !!key;

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    calendarAPI.convertBatch(key.split(','))
      .then(({ data }) => { if (!cancelled) setLabels(data.labels || {}); })
      .catch(() => { if (!cancelled) setLabels({}); });
    return () => { cancelled = true; };
  }, [active, key]);

  // A plain per-render check rather than resetting `labels` via setState
  // when switching away from BS (or to no dates) — avoids ever handing
  // back a stale BS label for what's now an AD pack, without an extra
  // effect-driven re-render to do it.
  return active ? labels : {};
}

// Every date-ish value in a plan's task tree — task/sub-task due dates,
// each entity-assignee's own deadline, and (kept, even though it's usually
// small/absent) recurrence bounds — worth batching through
// useBsDateLabels so a BS-mode pack's dates all resolve in one call rather
// than piecemeal as different components mount. A caller with a narrower
// need (a single TaskDetailPanel, say) is welcome to build its own smaller
// list instead; this is just the "give me everything in this plan" case.
export function collectPlanDates(tasks) {
  const out = [];
  for (const t of tasks || []) {
    if (t.due_date) out.push(t.due_date);
    for (const a of t.entity_assignees || []) {
      if (a.due_date) out.push(a.due_date);
    }
    for (const s of t.subtasks || []) {
      if (s.due_date) out.push(s.due_date);
      for (const a of s.entity_assignees || []) {
        if (a.due_date) out.push(a.due_date);
      }
    }
  }
  return out;
}

export function toneForDue(statusKey) {
  if (statusKey === 'overdue' || statusKey === 'rejected') return 'red';
  if (statusKey === 'duetoday') return 'amber';
  return 'gray';
}

// ----- Frequency label — mirrors the template editor's own vocabulary -----
// (TemplateItemFormModal's FREQUENCIES / CUSTOM_UNITS) so a period preview
// describes a task's cadence the same way an admin configured it: a plain
// name for the base frequencies, or "Custom — every N <units>" once
// interval_count > 1 (that's how "Custom" is stored — see
// TemplateItemFormModal's header comment).
const FREQ_BASE_LABEL = {
  DAILY: 'Daily', WEEKLY: 'Weekly', MONTHLY: 'Monthly',
  QUARTERLY: 'Quarterly', SEMI_ANNUAL: 'Semi-annual', ANNUAL: 'Annual',
};
const FREQ_CUSTOM_UNIT = {
  DAILY: 'days', WEEKLY: 'weeks', MONTHLY: 'months',
  QUARTERLY: 'quarters', SEMI_ANNUAL: 'half-years', ANNUAL: 'years',
};
export function freqLabel(frequency, intervalCount = 1) {
  if (!frequency) return 'Once per period';
  if (intervalCount > 1) {
    return `Custom — every ${intervalCount} ${FREQ_CUSTOM_UNIT[frequency] || frequency.toLowerCase()}`;
  }
  return FREQ_BASE_LABEL[frequency] || frequency;
}

// ----- Local-calendar-day ISO formatting -----
// `Date.prototype.toISOString()` always converts to UTC first — for anyone
// in a positive UTC-offset timezone (Nepal, +5:45, among others this app
// explicitly supports via the BS calendar), local midnight on day N is
// still the PREVIOUS day in UTC, so `someLocalMidnightDate.toISOString()
// .slice(0,10)` silently returns day N-1. That's a real, deterministic bug
// for date math built as "construct a local midnight Date, shift it by
// N days, read back the ISO string" (e.g. resolving a template's "day N of
// period" into a real calendar date) — it doesn't depend on what time it
// currently is, it mis-fires for every single date. `toLocalIso` reads the
// Date's own local getters instead, so the ISO string always matches the
// calendar day the Date was actually constructed to represent.
export function toLocalIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// Today, as the viewer's own local calendar date — the correct replacement
// for `new Date().toISOString().slice(0,10)` (which is wrong for part of
// the day, every day, for anyone east of UTC — see `toLocalIso`).
export function todayIso() {
  return toLocalIso(new Date());
}

// ----- "Time Logged Today" dashboard metric -----
// 8h/480min daily logging target, shared by the Team Lead and Team Member
// dashboard banners (see OverviewGrid).
export const DAILY_TIME_TARGET_MINUTES = 480;

// Minutes -> "4h 15m" / "0h 00m" (minutes always zero-padded to 2 digits),
// never negative even given a stray bad value.
export function formatLoggedDuration(totalMinutes) {
  const mins = Math.max(0, Math.round(totalMinutes || 0));
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${h}h ${String(m).padStart(2, '0')}m`;
}

// { pct, label } for the "Time Logged Today" card. `pct` is capped at 100
// so the bar never overflows once the daily target is exceeded — `label`
// still shows the real (possibly >8h) total, e.g. "9h 30m / 8h".
export function timeLoggedTodayStats(minutes, targetMinutes = DAILY_TIME_TARGET_MINUTES) {
  const logged = Math.max(0, Math.round(minutes || 0));
  const pct = targetMinutes ? Math.min(100, Math.round((logged / targetMinutes) * 100)) : 0;
  const targetHours = Math.round(targetMinutes / 60);
  return { pct, label: `${formatLoggedDuration(logged)} / ${targetHours}h` };
}

// Dropdown tag ("closed" / "upcoming") for a period picker, from the
// period's own date range rather than its backend `status`. Every
// not-yet-current period sits as CLOSED in the database until the
// scheduler actually reaches it (see apply_period_schedule /
// close_period on the backend), so a period that hasn't started yet
// would otherwise misread as "closed" instead of "upcoming".
export function periodDropdownTag(p, today = todayIso()) {
  if (!p?.start_date || !p?.end_date) return '';
  if (p.end_date < today) return 'closed';
  if (p.start_date > today) return 'upcoming';
  return '';
}
