import { useEffect, useMemo, useRef, useState } from 'react';
import Icon from '../ui/Icon';
import Badge from '../ui/Badge';
import Toggle from '../ui/Toggle';
import PriorityTag from '../ui/PriorityTag';
import { MiniProgress } from '../ui/ProgressBar';
import AssigneeCell from '../ui/AssigneeCell';
import BulkActionBar from './BulkActionBar';
import { sortByDeadline } from '../../pages/leadDashUtils';

const PRIORITY_RANK = { high: 0, med: 1, low: 2 };

// Column sort comparators — keyed by the same name passed to <Th sortKey>.
// Each returns a value that compares naturally with < / >.
const SORT_VALUE = {
  title: (r) => (r.title || '').toLowerCase(),
  due: (r) => r.dueDateRaw || '9999-99-99', // undated sorts last, both directions
  status: (r) => (r.status || ''),
  priority: (r) => PRIORITY_RANK[r.priority] ?? 9,
  tags: (r) => ((r.tags || []).join(', ').toLowerCase()),
};

// The table's natural order when no column is chosen (see sortByDeadline):
// due today first, then tomorrow and onward, undated next, and deadlines
// already missed at the bottom. Rows due the same day put higher priority
// first; anything still tied keeps the order it arrived in, so a refresh
// never shuffles equal rows.
const orderByDeadline = (rows) => sortByDeadline(rows, (r) => r.dueDateRaw, SORT_VALUE.priority);

// A row can be ticked for a bulk action when it's a leaf task (sub-task
// parents are assigned / reviewed per sub-task), not locked in an upcoming
// period, and not already completed.
const isBulkSelectable = (t) => !t.subtasks?.length && !t.isUpcoming && t.status !== 'completed';

// The backend refuses a bulk action over this many tasks (BULK_TASK_LIMIT in
// schemas.py), so the selection stops growing here rather than failing later.
const BULK_MAX = 200;

const TAG_CHIPS_VISIBLE = 2;

// Table-cell tag display — stays on one line at the row's normal height.
// Shows at most a couple of chips plus a "+N" overflow badge rather than
// wrapping (which used to blow up row height once a task had 3+ tags);
// the title attribute on the "+N" badge carries the rest for hover.
function TagChips({ tags }) {
  if (!tags || !tags.length) return <span className="lead-unassigned">—</span>;
  const shown = tags.slice(0, TAG_CHIPS_VISIBLE);
  const overflow = tags.slice(TAG_CHIPS_VISIBLE);
  return (
    <div className="tag-chip-list">
      {shown.map((t) => <span className="tag-chip" key={t} title={t}>{t}</span>)}
      {overflow.length > 0 && (
        <span className="tag-chip tag-chip-more" title={overflow.join(', ')}>
          +{overflow.length}
        </span>
      )}
    </div>
  );
}

// Clickable column header — cycles unsorted -> asc -> desc -> unsorted.
function Th({ label, sortKey, sort, onSort, style, align }) {
  if (!sortKey) return <th style={style}>{label}</th>;
  const active = sort.by === sortKey;
  return (
    <th
      style={{ ...style, cursor: 'pointer', userSelect: 'none', textAlign: align }}
      onClick={() => onSort(sortKey)}
      title="Click to sort"
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        {label}
        <Icon
          name={active && sort.dir === 'desc' ? 'chevron-down' : active ? 'chevron-down' : 'sort'}
          size={active ? 11 : 12}
          style={{
            opacity: active ? 1 : 0.35,
            transform: active && sort.dir === 'asc' ? 'rotate(180deg)' : undefined,
          }}
        />
      </span>
    </th>
  );
}

// Nested rows shown when a task is expanded — a read-only summary in every
// mode (name / desc / assignee / due / status). They are NOT clickable:
// the whole task row opens the detail panel, where every sub-task lives in
// one overview; there is no separate per-sub-task panel.
function SubtaskRows({ subtasks, member, meId, withSelect }) {
  // Same reading order as the task rows above; ties keep the authored order.
  const ordered = useMemo(() => sortByDeadline(subtasks, (s) => s.dueDateRaw), [subtasks]);
  return (
    <>
      <tr className="subtask-row">
        {withSelect && <td className="subtask-header-cell" />}
        <td className="subtask-header-cell" />
        <td className="subtask-header-cell">Subtask</td>
        <td className="subtask-header-cell">Description</td>
        <td className="subtask-header-cell">Assignee</td>
        <td className="subtask-header-cell">Due date</td>
        {member ? (
          <>
            <td className="subtask-header-cell">Status</td>
            <td className="subtask-header-cell" colSpan={3} />
          </>
        ) : (
          <td className="subtask-header-cell" colSpan={4} />
        )}
      </tr>
      {ordered.map((st, i) => {
        return (
          <tr className="subtask-row" key={st.id ?? i}>
            {withSelect && <td />}
            <td />
            <td className="task-cell subtask-name-cell">{st.name}</td>
            <td className="desc-cell">{st.desc}</td>
            <td className="assignee-cell"><AssigneeCell people={st.assignees ?? []} highlightId={meId} /></td>
            <td className="due-cell"><div className="dd">{st.due}</div></td>
            {member ? (
              <>
                <td>{st.status ? <Badge status={st.status} lateDays={st.lateDays} /> : null}</td>
                <td colSpan={3} />
              </>
            ) : (
              <td colSpan={4} />
            )}
          </tr>
        );
      })}
    </>
  );
}

// Kebab (⋮) row menu — Edit / Edit subtasks / Delete / Assign to whole
// entity / Assign to specific people. Uses a fixed-positioned dropdown so
// it isn't clipped by the table's horizontal scroll container.
function RowMenu({
  task, members = [], onEdit, onEditSubtasks, onDelete, onAssignEntity, onAssignMulti, onAssignSeries,
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(null); // null | 'delete' | 'assign' | 'multi'
  const [pos, setPos] = useState({ top: 0, right: 0 });
  const [multiSel, setMultiSel] = useState(() => new Set());
  const [applyAll, setApplyAll] = useState(true);
  const wrapRef = useRef(null);

  const hasSubtasks = task.subtasks?.length > 0;
  const seriesSize = task.seriesSize || 0; // >1 -> part of a recurring series
  const canEditSubtasks = !!onEditSubtasks && hasSubtasks && !task.isUpcoming;
  const canAssignEntity = !!onAssignEntity
    && !hasSubtasks
    && !task.isUpcoming
    && task.status !== 'completed';
  const canAssignMulti = !!onAssignMulti
    && members.length > 0
    && !hasSubtasks
    && !task.isUpcoming
    && task.status !== 'completed';

  const currentAssigneeIds = () => {
    const raw = task.raw || {};
    if (raw.assigned_entity_wide) return (raw.entity_assignees || []).map((a) => a.user_id);
    if (raw.member_assigned_to_id) return [raw.member_assigned_to_id];
    return (task.assignees || []).map((p) => p.id).filter(Boolean);
  };
  const openMulti = () => {
    setMultiSel(new Set(currentAssigneeIds()));
    setApplyAll(seriesSize > 1 && !!onAssignSeries);
    setConfirming('multi');
  };
  const toggleMulti = (id) => setMultiSel((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => {
      if (e.type === 'scroll') {
        // Scrolling inside the menu's own list must not close it.
        if (wrapRef.current?.contains(e.target)) return;
        setOpen(false);
        setConfirming(null);
        return;
      }
      if (!wrapRef.current?.contains(e.target)) {
        setOpen(false);
        setConfirming(null);
      }
    };
    document.addEventListener('mousedown', close);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', close);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  const toggle = (e) => {
    e.stopPropagation();
    const r = e.currentTarget.getBoundingClientRect();
    setPos({ top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) });
    setConfirming(null);
    setOpen((o) => !o);
  };

  return (
    <div className="kebab-wrap" ref={wrapRef} onClick={(e) => e.stopPropagation()}>
      <div className="kebab" onClick={toggle} aria-label="Row actions">
        <Icon name="kebab" size={15} />
      </div>
      {open && (
        <div
          className="kebab-menu"
          style={{ top: pos.top, right: pos.right, ...(confirming === 'multi' ? { minWidth: 232 } : {}) }}
        >
          {!confirming ? (
            <>
              <button type="button" onClick={() => { setOpen(false); onEdit(task); }}>
                <Icon name="clipboard" size={13} /> Edit task
              </button>
              {canEditSubtasks && (
                <button type="button" onClick={() => { setOpen(false); onEditSubtasks(task); }}>
                  <Icon name="edit" size={13} /> Edit subtasks
                </button>
              )}
              {canAssignMulti && (
                <button type="button" onClick={openMulti}>
                  <Icon name="layers" size={13} /> Assign to specific people
                </button>
              )}
              {canAssignEntity && (
                <button type="button" onClick={() => setConfirming('assign')}>
                  <Icon name="layers" size={13} /> Assign to whole entity
                </button>
              )}
              {task.isCustom ? (
                <button type="button" className="danger" onClick={() => setConfirming('delete')}>
                  <Icon name="close" size={13} strokeWidth={2.3} /> Delete task
                </button>
              ) : (
                <div className="km-confirm">Template task — cannot delete</div>
              )}
            </>
          ) : confirming === 'multi' ? (
            <>
              <div className="km-confirm">Assign “{task.title}” to:</div>
              <div className="km-checklist">
                {members.map((m) => (
                  <label key={m.id} className="km-check">
                    <input
                      type="checkbox"
                      checked={multiSel.has(m.id)}
                      onChange={() => toggleMulti(m.id)}
                    />
                    {m.name}
                  </label>
                ))}
              </div>
              {seriesSize > 1 && !!onAssignSeries && (
                <label className="km-check km-check-series">
                  <input
                    type="checkbox"
                    checked={applyAll}
                    onChange={() => setApplyAll((v) => !v)}
                  />
                  Apply to all {seriesSize} occurrences of this recurring task
                </label>
              )}
              <button
                type="button" className="btn-primary-menu"
                disabled={multiSel.size === 0}
                onClick={() => {
                  setOpen(false);
                  setConfirming(null);
                  if (seriesSize > 1 && applyAll && onAssignSeries) {
                    onAssignSeries(task, [...multiSel]);
                  } else {
                    onAssignMulti(task, [...multiSel]);
                  }
                }}
              >
                Save assignment
              </button>
              <button type="button" onClick={() => setConfirming(null)}>Cancel</button>
            </>
          ) : confirming === 'assign' ? (
            <>
              <div className="km-confirm">
                Assign “{task.title}” to every Team Member on this pack's entity?
              </div>
              <button
                type="button" className="btn-primary-menu"
                onClick={() => { setOpen(false); setConfirming(null); onAssignEntity(task); }}
              >
                Yes, assign to everyone
              </button>
              <button type="button" onClick={() => setConfirming(null)}>Cancel</button>
            </>
          ) : (
            <>
              <div className="km-confirm">Delete “{task.title}”?</div>
              <button
                type="button" className="danger"
                onClick={() => { setOpen(false); setConfirming(null); onDelete(task); }}
              >
                Yes, delete
              </button>
              <button type="button" onClick={() => setConfirming(null)}>Cancel</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// `task` is a view-model built by the parent (LeadPackWorkspace.toRow /
// MemberPackView.toRow).  `mode`: 'lead' (default) | 'member'.
function Row({
  task, sn, readOnly, mode, members, meId, onOpenDetail, onReview, onApprove, onEdit, onEditSubtasks, onDelete,
  onAssignEntity, onAssignMulti, onAssignSeries, onComplete, onReopen, onWithdraw,
  withSelect, selected, onToggleSelect,
}) {
  const [expanded, setExpanded] = useState(!!task.defaultExpanded);

  const member = mode === 'member';
  const status = task.status;
  const hasSubtasks = task.subtasks?.length > 0;
  const people = task.assignees ?? [];

  // Which toggle interaction (if any) this row currently supports.
  // 'submit'  — idle → open the submit-for-review flow  (member)
  // 'withdraw'— amber → pull an un-reviewed submission back to In Progress
  // 'approve' — amber → the lead approves the submission in one click
  // 'review'  — amber → open the approve / reject modal (rolled-up parents)
  // 'complete'— idle → the lead marks their own assigned work done
  // 'reopen'  — on  → the lead un-approves a completed leaf task
  let toggleAction = null;
  if (member) {
    if (task.canSubmit) toggleAction = 'submit';
    else if (status === 'review' && task.canWithdraw) toggleAction = 'withdraw';
  } else if (!readOnly) {
    if (task.canComplete) toggleAction = 'complete';
    else if (status === 'review' && task.canReview) {
      // Always open the review dialog (which shows the submitter's time
      // taken + notes) rather than approving in one silent click.
      toggleAction = 'review';
    } else if (status === 'completed' && task.reopenable) toggleAction = 'reopen';
  }
  // A one-click "approve" toggle keeps reject-with-reason one tap away.
  const showReject = toggleAction === 'approve' && !!onReview;

  // A "parent of my sub-tasks" row (member view) is informational only —
  // its sub-tasks are worked from their own detail panels. It still shows
  // a (read-only) completed pill once every sub-task of mine is approved,
  // so the member can see the whole task landed.
  const showToggle = task.parentOfMine
    ? status === 'completed'
    : member
      ? (task.canSubmit || status === 'review' || status === 'completed')
      : (!readOnly && (status === 'review' || status === 'completed' || task.canComplete));
  const toggleVariant = status === 'completed' ? 'on' : status === 'review' ? 'review' : '';
  const toggleDisabled = showToggle && !toggleAction;
  const toggleTitle = {
    submit: 'Submit for review',
    withdraw: 'Withdraw — back to In Progress',
    approve: 'Approve this submission',
    review: 'Review this submission',
    complete: 'Mark my work done',
    reopen: 'Reopen — send back for review',
  }[toggleAction] || (status === 'completed' ? 'Completed' : status === 'review' ? 'Awaiting review' : '');

  const onToggle = () => {
    switch (toggleAction) {
      case 'submit': onReview(task); break;
      case 'withdraw': onWithdraw?.(task); break;
      case 'approve': onApprove?.(task); break;
      case 'review': onReview(task); break;
      // A task assigned to the viewing Lead themselves — complete it the
      // same way a Team Member submits, rather than opening the
      // approve/reject review modal (there's no one else's decision to make).
      case 'complete': onComplete?.(task); break;
      case 'reopen': onReopen?.(task); break;
      default: break;
    }
  };

  return (
    <>
      <tr className={`task-row${selected ? ' row-selected' : ''}`} onClick={() => onOpenDetail(task)}>
        {withSelect && (
          <td className="select-cell" onClick={(e) => e.stopPropagation()}>
            {isBulkSelectable(task) && (
              <input
                type="checkbox"
                checked={!!selected}
                onChange={() => onToggleSelect(task.id)}
                aria-label={`Select ${task.title}`}
              />
            )}
          </td>
        )}
        <td className="sn-cell">{sn}</td>

        <td className="task-cell">
          <div className="task-title">
            {task.title}
            {!task.isRequired && (
              <span className="optional-pill" title="Optional — missing it never counts as overdue">
                Optional
              </span>
            )}
          </div>
          {task.sub && <div className="task-sub">{task.sub}</div>}
          {hasSubtasks && (
            <div
              className={`subtask-tag${expanded ? ' expanded' : ''}`}
              role="button"
              tabIndex={0}
              aria-expanded={expanded}
              onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  e.stopPropagation();
                  setExpanded((v) => !v);
                }
              }}
            >
              <span className="st-icon"><Icon name="layers" size={13} /></span>
              <span className="st-count">{task.subtasks.length}</span>
              {' '}
              {expanded ? 'Hide' : 'Show'} sub-task{task.subtasks.length > 1 ? 's' : ''}
              <span className="chevron"><Icon name="chevron-down" size={11} strokeWidth={2.6} /></span>
            </div>
          )}
          {hasSubtasks && (
            <div className="task-cell-progress">
              <MiniProgress value={task.prog} blue />
            </div>
          )}
        </td>

        <td className="desc-cell">{task.desc}</td>
        <td className="assignee-cell"><AssigneeCell people={people} highlightId={meId} /></td>

        <td className="due-cell">
          <div className="dd">{task.due}</div>
          <div className="dleft" style={status === 'overdue' ? { color: 'var(--red)' } : undefined}>
            {task.dmeta}
          </div>
        </td>

        <td><Badge status={status} lateDays={task.lateDays} /></td>
        <td><PriorityTag priority={task.priority} /></td>
        <td className="tags-cell"><TagChips tags={task.tags} /></td>
        <td>
          <div className="end-cell">
            {showReject && (
              <button
                type="button"
                className="row-reject-btn"
                title="Reject with a reason"
                onClick={(e) => { e.stopPropagation(); onReview(task); }}
              >
                <Icon name="close" size={13} strokeWidth={2.5} />
              </button>
            )}
            {showToggle && (
              <Toggle
                variant={toggleVariant}
                disabled={toggleDisabled}
                title={toggleTitle}
                onClick={onToggle}
              />
            )}
            {!member && (
              readOnly ? (
                <div className="kebab" onClick={(e) => e.stopPropagation()}>
                  <Icon name="kebab" size={15} />
                </div>
              ) : (
                <RowMenu
                  task={task}
                  members={members}
                  onEdit={onEdit}
                  onEditSubtasks={onEditSubtasks}
                  onDelete={onDelete}
                  onAssignEntity={onAssignEntity}
                  onAssignMulti={onAssignMulti}
                  onAssignSeries={onAssignSeries}
                />
              )
            )}
          </div>
        </td>
      </tr>
      {expanded && hasSubtasks && (
        <SubtaskRows
          subtasks={task.subtasks}
          member={member && !!task.parentOfMine}
          meId={meId}
          withSelect={withSelect}
        />
      )}
    </>
  );
}

const PAGE_SIZE_OPTIONS = [5, 10, 15, 20, 50, 75];
// Remembered per-viewer in this browser (not per-user account) so a page
// refresh doesn't silently reset the Lead's chosen rows-per-page back to
// the 5-row default. Wrapped in try/catch — private-browsing / blocked
// storage should never break the table, just fall back to the default.
const PAGE_SIZE_STORAGE_KEY = 'closeloop:allTasksTable:pageSize';

function loadStoredPageSize() {
  try {
    const n = Number(localStorage.getItem(PAGE_SIZE_STORAGE_KEY));
    return PAGE_SIZE_OPTIONS.includes(n) ? n : PAGE_SIZE_OPTIONS[0];
  } catch {
    return PAGE_SIZE_OPTIONS[0];
  }
}

export default function AllTasksTable({
  rows, readOnly = false, mode = 'lead', members = [], meId, onOpenDetail, onReview, onApprove, onEdit,
  onEditSubtasks, onDelete, onAssignEntity, onAssignMulti, onAssignSeries, onComplete, onReopen, onWithdraw,
  // Team Lead bulk actions — the checkbox column + action bar only exist
  // when BOTH are passed (and the table is editable, lead mode). Each
  // returns a Promise<boolean>: true = done, so the selection is cleared.
  onBulkAssign, onBulkReview,
  // Identifies the current tab/filter selection (e.g. `${statusTab}|${tagFilter}`
  // from the parent) — the page resets to 1 when THIS changes, not merely
  // when `rows` gets a new array reference. `rows` is recreated on every
  // data reload (after any edit/assign/submit/etc.), so keying the reset
  // off it instead used to bounce the Lead back to page 1 after every
  // change, losing their place in a long task list.
  resetKey = '',
}) {
  const [sort, setSort] = useState({ by: null, dir: 'asc' });
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(() => new Set());
  const withSelect = mode === 'lead' && !readOnly && !!onBulkAssign && !!onBulkReview;
  const [pageSize, setPageSizeState] = useState(loadStoredPageSize);
  const setPageSize = (n) => {
    setPageSizeState(n);
    try { localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(n)); } catch { /* ignore */ }
  };

  const onSort = (key) => {
    setSort((prev) => {
      if (prev.by !== key) return { by: key, dir: 'asc' };
      if (prev.dir === 'asc') return { by: key, dir: 'desc' };
      return { by: null, dir: 'asc' }; // third click clears back to deadline order
    });
  };

  // Deadline order is the base: a column sort below keeps it for rows that
  // tie on the chosen column (e.g. sorting by status keeps each status
  // group in deadline order).
  const deadlineRows = useMemo(() => orderByDeadline(rows), [rows]);
  const sortedRows = useMemo(() => {
    if (!sort.by) return deadlineRows;
    const val = SORT_VALUE[sort.by];
    const withIndex = deadlineRows.map((r, i) => ({ r, i }));
    withIndex.sort((a, b) => {
      const av = val(a.r);
      const bv = val(b.r);
      if (av < bv) return sort.dir === 'asc' ? -1 : 1;
      if (av > bv) return sort.dir === 'asc' ? 1 : -1;
      return a.i - b.i; // stable
    });
    return withIndex.map((x) => x.r);
  }, [deadlineRows, sort]);

  // The pagination bar (incl. the rows-per-page picker) shows once a view
  // has more than the SMALLEST page size worth of rows — independent of
  // whatever page size is currently selected, so picking a larger page
  // size (e.g. 20) on a list that then fits on one page doesn't make the
  // picker itself disappear, stranding the Lead unable to shrink it back
  // down. Sub-task rows are rendered nested inside their parent's <Row>,
  // so they don't count here.
  const showPagination = sortedRows.length > PAGE_SIZE_OPTIONS[0];
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / pageSize));

  // Reset to the first page when the Lead actually changes what they're
  // looking at (tab / tag filter), re-sorts, or changes the page size —
  // NOT on every data refresh, so an edit/assign/submit/etc. leaves them
  // on the same page instead of bouncing back to page 1. If a refresh
  // happens to shrink the page count out from under the current page,
  // clamp down to the last valid page.
  useEffect(() => { setPage(1); }, [resetKey, sort, pageSize]);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const offset = (page - 1) * pageSize;
  const visibleRows = sortedRows.slice(offset, offset + pageSize);

  // Selection follows what the Lead is looking at: a new tab / tag filter
  // starts clean, and after any data reload ids whose row vanished (or is
  // no longer actionable, e.g. just approved) quietly drop out — without
  // clearing the rest, so a reload doesn't throw away the selection.
  useEffect(() => { setSelected(new Set()); }, [resetKey]);
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const live = new Set(rows.filter(isBulkSelectable).map((r) => r.id));
      const next = new Set([...prev].filter((id) => live.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [rows]);

  const toggleSelect = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else if (next.size < BULK_MAX) next.add(id);
    return next;
  });
  const pageSelectable = visibleRows.filter(isBulkSelectable);
  const pageAllSelected = pageSelectable.length > 0 && pageSelectable.every((r) => selected.has(r.id));
  const pageSomeSelected = pageSelectable.some((r) => selected.has(r.id));
  const toggleSelectPage = () => setSelected((prev) => {
    const next = new Set(prev);
    if (pageAllSelected) {
      pageSelectable.forEach((r) => next.delete(r.id));
    } else {
      for (const r of pageSelectable) {
        if (next.size >= BULK_MAX) break;
        next.add(r.id);
      }
    }
    return next;
  });
  const selectedRows = sortedRows.filter((r) => selected.has(r.id));
  const clearSelection = () => setSelected(new Set());
  const bulkAssign = async (selRows, userIds, scope) => {
    const ok = await onBulkAssign(selRows, userIds, scope);
    if (ok) clearSelection();
    return ok;
  };
  const bulkReview = async (selRows, decision, note) => {
    const ok = await onBulkReview(selRows, decision, note);
    if (ok) clearSelection();
    return ok;
  };

  return (
    <>
    {withSelect && selectedRows.length > 0 && (
      <BulkActionBar
        rows={selectedRows}
        atLimit={selectedRows.length >= BULK_MAX}
        members={members}
        onAssign={bulkAssign}
        onReview={bulkReview}
        onClear={clearSelection}
      />
    )}
    <table>
      <thead>
        <tr>
          {withSelect && (
            <th className="select-cell" style={{ width: 36 }}>
              <input
                type="checkbox"
                checked={pageAllSelected}
                ref={(el) => { if (el) el.indeterminate = pageSomeSelected && !pageAllSelected; }}
                disabled={pageSelectable.length === 0}
                onChange={toggleSelectPage}
                aria-label="Select all tasks on this page"
              />
            </th>
          )}
          <th style={{ width: 44 }}>SN</th>
          <Th label="Task" sortKey="title" sort={sort} onSort={onSort} />
          <th>Description</th>
          <th>Assignee</th>
          <Th label="Due Date" sortKey="due" sort={sort} onSort={onSort} />
          <Th label="Status" sortKey="status" sort={sort} onSort={onSort} />
          <Th label="Priority" sortKey="priority" sort={sort} onSort={onSort} />
          <Th label="Tags" sortKey="tags" sort={sort} onSort={onSort} />
          <th style={{ textAlign: 'right' }}>{mode === 'member' ? 'Submit' : 'Review'}</th>
        </tr>
      </thead>
      <tbody>
        {sortedRows.length === 0 ? (
          <tr>
            <td colSpan={withSelect ? 10 : 9} style={{ textAlign: 'center', color: 'var(--gray-mute)', padding: 32 }}>
              No tasks in this view.
            </td>
          </tr>
        ) : (
          visibleRows.map((task, i) => (
            <Row
              key={task.id ?? i}
              task={task}
              sn={offset + i + 1}
              readOnly={readOnly}
              mode={mode}
              members={members}
              meId={meId}
              onOpenDetail={onOpenDetail}
              onReview={onReview}
              onApprove={onApprove}
              onEdit={onEdit}
              onEditSubtasks={onEditSubtasks}
              onDelete={onDelete}
              onAssignEntity={onAssignEntity}
              onAssignMulti={onAssignMulti}
              onAssignSeries={onAssignSeries}
              onComplete={onComplete}
              onReopen={onReopen}
              onWithdraw={onWithdraw}
              withSelect={withSelect}
              selected={selected.has(task.id)}
              onToggleSelect={toggleSelect}
            />
          ))
        )}
      </tbody>
    </table>
    {showPagination && (
      <div className="table-pagination">
        <span className="tp-info">
          {offset + 1}–{Math.min(offset + pageSize, sortedRows.length)} of {sortedRows.length}
        </span>
        <div className="tp-controls">
          <label className="tp-page-size">
            Rows:
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="tp-btn"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <Icon name="chevron-left" size={13} strokeWidth={2.4} /> Prev
          </button>
          <span className="tp-page">Page {page} / {pageCount}</span>
          <button
            type="button"
            className="tp-btn"
            disabled={page >= pageCount}
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
          >
            Next <Icon name="chevron-right" size={13} strokeWidth={2.4} />
          </button>
        </div>
      </div>
    )}
    </>
  );
}
