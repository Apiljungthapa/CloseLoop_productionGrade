// Team Lead pack workspace — the "new and improved" pack detail view.
// Design ported from frontend_samrat, wired to planAPI.get(id) (full task
// tree + can_edit) plus the task/subtask mutation endpoints.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { planAPI, userAPI, taskAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import Avatar from '../components/ui/Avatar';
import { ProgressBar } from '../components/ui/ProgressBar';
import StatusTabs from '../components/tasklist/StatusTabs';
import AllTasksTable from '../components/alltasks/AllTasksTable';
import AddTaskModal from '../components/modals/AddTaskModal';
import EditTaskModal from '../components/modals/EditTaskModal';
import EditSubtasksModal from '../components/modals/EditSubtasksModal';
import ReviewModal from '../components/modals/ReviewModal';
import SubmitTaskModal from '../components/member/SubmitTaskModal';
import TaskDetailPanel from '../components/detail/TaskDetailPanel';
import {
  buildPlanTaskRows, buildSubtaskSeriesCounts, collectPlanDates, useBsDateLabels, dueWeekBucket,
  initialsOf, colorFor, fmtDate, packStatus, STATUS_TABS, inStatusTab,
} from './leadDashUtils';

export default function LeadPackWorkspace() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const { user } = useAuth();

  const [plan, setPlan] = useState(null);
  const [members, setMembers] = useState([]);
  const [err, setErr] = useState('');
  const [seriesFlash, setSeriesFlash] = useState('');
  const [statusTab, setStatusTab] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [taskSearch, setTaskSearch] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [editTask, setEditTask] = useState(null);
  const [editSubtasksTask, setEditSubtasksTask] = useState(null);
  const [detailTaskId, setDetailTaskId] = useState(null);
  const [reviewItem, setReviewItem] = useState(null);
  const [completeItem, setCompleteItem] = useState(null);

  const load = useCallback(async () => {
    try {
      const [planR, usersR] = await Promise.all([planAPI.get(id), userAPI.list()]);
      setPlan(planR.data);
      setMembers(usersR.data.filter((u) => u.role === 'TEAM_MEMBER'));
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not load this pack.'));
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  useEffect(() => {
    if (!seriesFlash) return undefined;
    const t = setTimeout(() => setSeriesFlash(''), 5000);
    return () => clearTimeout(t);
  }, [seriesFlash]);

  const deleteTask = async (row) => {
    try {
      await taskAPI.delete(row.id);
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not delete the task.'));
    }
  };

  const assignEntity = async (row) => {
    try {
      await taskAPI.assignEntity(row.id);
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not assign this task to the whole entity.'));
    }
  };

  // Assign a task to a hand-picked set of Team Members — same multi-assignee
  // mechanics as "whole entity" (everyone marks their own part done, the
  // task auto-submits once all have).
  const assignMulti = async (row, userIds) => {
    try {
      await taskAPI.assignMulti(row.id, userIds);
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not assign this task to those people.'));
    }
  };

  // Assign EVERY occurrence of a recurring task (a daily/weekly task that
  // materialized into many rows for this period) in one go, instead of
  // repeating the same assignment per row.
  const assignSeries = async (row, userIds) => {
    try {
      const { data } = await taskAPI.assignSeries(row.id, userIds);
      setErr('');
      setSeriesFlash(
        `Assigned ${data?.updated ?? 0} occurrence${data?.updated === 1 ? '' : 's'} of "${row.title}"`
        + (data?.skipped ? ` — ${data.skipped} left as-is (already done or in review).` : '.'),
      );
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not assign every occurrence of this task.'));
    }
  };

  // Summarise a bulk result: "<done>" plus how many were skipped and why
  // (grouped by reason so a 50-row batch doesn't print 50 lines).
  const bulkSummary = (done, data) => {
    const skipped = data?.skipped || [];
    if (skipped.length === 0) return `${done}.`;
    const byReason = {};
    for (const sk of skipped) byReason[sk.reason] = (byReason[sk.reason] || 0) + 1;
    const why = Object.entries(byReason).map(([r, n]) => `${n} — ${r}`).join('; ');
    return `${done}. ${skipped.length} skipped (${why}).`;
  };

  // Bulk assign / unassign the ticked rows (no userIds = unassign). Returns
  // true when the request went through so the table clears its selection.
  const bulkAssign = async (rows, userIds, scope) => {
    try {
      const { data } = await taskAPI.bulkAssign(rows.map((r) => r.id), userIds, scope);
      setErr('');
      const n = data?.updated ?? 0;
      setSeriesFlash(bulkSummary(
        `${userIds.length ? 'Assigned' : 'Unassigned'} ${n} task${n === 1 ? '' : 's'}`, data,
      ));
      load();
      return true;
    } catch (e) {
      setErr(getErrorMessage(e, userIds.length
        ? 'Could not assign the selected tasks.'
        : 'Could not unassign the selected tasks.'));
      return false;
    }
  };

  // Bulk approve / reject the ticked rows (reject needs one shared note).
  const bulkReview = async (rows, decision, note) => {
    try {
      const { data } = await taskAPI.bulkReview(rows.map((r) => r.id), decision, note);
      setErr('');
      const n = data?.updated ?? 0;
      setSeriesFlash(bulkSummary(
        `${decision === 'APPROVE' ? 'Accepted' : 'Rejected'} ${n} task${n === 1 ? '' : 's'}`, data,
      ));
      load();
      return true;
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not review the selected tasks.'));
      return false;
    }
  };

  // A task assigned to multiple people (whole-entity or a hand-picked
  // set) — the Lead marks their OWN part done exactly like any other
  // assignee; it only completes once everyone's done that. A plain
  // single-assignee task instead opens the usual submit-for-review modal.
  // Either way, Time Taken and Notes are required from a Lead's own
  // completion — there's no separate reviewer to catch a missing value.
  const completeRow = (row) => {
    setCompleteItem({ id: row.id, kind: 'TASK', title: row.title, entityWide: !!row.completeViaEntity });
  };

  // Un-approve a completed leaf task — flip it back to Pending Review so
  // the Lead can reject it (sending it back to the member to redo and
  // resubmit) or approve it again.
  const reopenRow = async (row) => {
    try {
      await taskAPI.update(row.id, { status: 'PENDING_REVIEW' });
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not reopen this task.'));
    }
  };

  // One-click approve straight from the row toggle (reject still goes
  // through the modal, where a reason is required).
  const approveRow = async (row) => {
    try {
      await taskAPI.review(row.id, { decision: 'APPROVE' });
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not approve this task.'));
    }
  };

  const reviewRow = (row) => {
    setReviewItem({
      id: row.id,
      kind: 'TASK',
      title: row.title,
      assignee: row.raw.assigned_entity_wide ? null : row.assignees[0]?.name,
      submittedAt: row.raw.assigned_entity_wide ? null : row.raw.submitted_at,
      completionNotes: row.raw.assigned_entity_wide ? null : row.raw.completion_notes,
      completionMinutes: row.raw.assigned_entity_wide ? null : row.raw.completion_time_minutes,
      entityAssignees: row.raw.assigned_entity_wide ? row.raw.entity_assignees : null,
    });
  };

  const openDetail = (row) => {
    setDetailTaskId(row.id);
  };

  const editable = plan?.can_edit !== false && plan?.owner?.id === user.id;
  // This pack's own dates (task/sub-task due dates, per-assignee deadlines)
  // rendered in whichever calendar the pack itself is configured for — a
  // BS pack shows BS everywhere here, an AD pack is untouched.
  const planDates = useMemo(
    () => (plan?.due_date ? [plan.due_date] : []).concat(collectPlanDates(plan?.tasks || [])),
    [plan],
  );
  const bsLabels = useBsDateLabels(planDates, plan?.calendar_type);
  const rows = useMemo(
    () => buildPlanTaskRows(
      plan?.tasks || [], user.id, plan?.period_start_date, plan?.period_end_date || plan?.due_date,
      bsLabels,
    ),
    [plan, user.id, bsLabels],
  );

  // Per-sub-task occurrence counts, so EditSubtasksModal can offer the same
  // "apply to every occurrence" default as the task-level assignee picker.
  const subtaskSeriesCounts = useMemo(
    () => buildSubtaskSeriesCounts(plan?.tasks || []),
    [plan],
  );

  // Deep link from elsewhere (e.g. the dashboard's deadline tables):
  // /packs/:id?task=<id> opens that task's detail panel (always a top-level
  // task id — TaskDetailPanel shows its sub-tasks inside it, same as a
  // normal click) and switches to its own status tab, so the table
  // underneath lands on the same "page" the task actually lives on.
  // Carried through from LeadDashboard so "Overview" below returns to
  // whichever period the lead was viewing rather than the current one.
  const periodParam = searchParams.get('period') || '';
  const backToOverview = orgPath(`/${periodParam ? `?period=${periodParam}` : ''}`);

  const openTaskId = searchParams.get('task');
  useEffect(() => {
    if (!openTaskId) return;
    const r = rows.find((x) => x.id === openTaskId);
    if (!r) return;
    setStatusTab(r.status);
    setDetailTaskId(r.id);
    setSearchParams((p) => {
      const n = new URLSearchParams(p);
      n.delete('task');
      return n;
    }, { replace: true });
  }, [openTaskId, rows]);

  const stats = useMemo(() => {
    // "Scheduled" placeholders aren't real work for this period, and skipped
    // tasks (optional, past due, not done) are listed but never counted.
    const workRows = rows.filter((r) => !r.isScheduled && r.status !== 'skipped');
    const total = workRows.length;
    const completed = workRows.filter((r) => r.status === 'completed').length;
    return {
      total,
      completed,
      review: workRows.filter((r) => r.status === 'review').length,
      overdue: workRows.filter((r) => r.status === 'overdue').length,
      entityCount: plan?.entity_name ? 1 : 0,
      pct: Math.round(plan?.progress ?? (total ? (completed / total) * 100 : 0)),
    };
  }, [rows, plan]);

  const status = plan ? packStatus(plan, stats) : null;

  // "Due This Week" / "Due Next Week" — Lead-workspace-only buckets layered
  // on top of the shared taskStatusKey buckets (not STATUS_TABS entries, so
  // Admin/Member views are untouched). Weeks run Sunday–Saturday in Nepal
  // time, matching dueMeta: This Week = tomorrow through Saturday, Next Week
  // = the following Sunday–Saturday. Only not-started/in-progress tasks
  // count (today's tasks live in "Due Today", past ones in "Overdue"), so
  // the three tabs never overlap.
  const weekBucketOf = useCallback((row) => {
    if (row.status !== 'notstarted' && row.status !== 'progress') return null;
    return dueWeekBucket(row.dueDateRaw);
  }, []);
  const isDueThisWeek = useCallback((row) => weekBucketOf(row) === 'thisweek', [weekBucketOf]);
  const isDueNextWeek = useCallback((row) => weekBucketOf(row) === 'nextweek', [weekBucketOf]);

  const tabs = useMemo(() => {
    const base = STATUS_TABS.map((t) => ({
      status: t.status,
      label: t.label,
      count: rows.filter((r) => inStatusTab(r.status, t.status)).length,
    }));
    const dueTodayIdx = base.findIndex((t) => t.status === 'duetoday');
    base.splice(
      dueTodayIdx + 1, 0,
      { status: 'duethisweek', label: 'Due This Week', count: rows.filter(isDueThisWeek).length },
      { status: 'duenextweek', label: 'Due Next Week', count: rows.filter(isDueNextWeek).length },
    );
    return base;
  }, [rows, isDueThisWeek, isDueNextWeek]);

  const allTags = useMemo(
    () => [...new Set(rows.flatMap((r) => r.tags || []))].sort((a, b) => a.localeCompare(b)),
    [rows],
  );
  const tabRows = useMemo(() => {
    const q = taskSearch.trim().toLowerCase();
    const matchesTab = (r) => {
      if (statusTab === 'duethisweek') return isDueThisWeek(r);
      if (statusTab === 'duenextweek') return isDueNextWeek(r);
      return inStatusTab(r.status, statusTab);
    };
    const out = rows
      .filter(matchesTab)
      .filter((r) => !tagFilter || (r.tags || []).includes(tagFilter))
      .filter((r) => !q || (
        r.title.toLowerCase().includes(q)
        || (r.desc || '').toLowerCase().includes(q)
        || (r.tags || []).some((t) => t.toLowerCase().includes(q))
        || r.assignees.some((a) => a.name?.toLowerCase().includes(q))
      ));
    // The due-date tabs read as a schedule: soonest first, then highest
    // priority, then title. (Column-header sorting still overrides this.)
    if (statusTab === 'duetoday' || statusTab === 'duethisweek' || statusTab === 'duenextweek') {
      const rank = { high: 0, med: 1, low: 2 };
      out.sort((a, b) => (
        (a.dueDateRaw || '9999-99-99').localeCompare(b.dueDateRaw || '9999-99-99')
        || (rank[a.priority] ?? 9) - (rank[b.priority] ?? 9)
        || (a.title || '').localeCompare(b.title || '')
      ));
    }
    return out;
  }, [rows, statusTab, tagFilter, taskSearch, isDueThisWeek, isDueNextWeek]);
  const unassignedCount = rows.filter((r) => r.assignees.length === 0).length;

  const team = useMemo(() => {
    const counts = new Map();
    for (const r of rows) {
      const people = r.subtasks.length
        ? r.subtasks.flatMap((s) => s.assignees)
        : r.assignees;
      for (const p of people) {
        if (!p) continue;
        const cur = counts.get(p.id) || { ...p, count: 0 };
        cur.count += 1;
        counts.set(p.id, cur);
      }
    }
    return [...counts.values()].sort((a, b) => b.count - a.count);
  }, [rows]);

  if (err && !plan) {
    return (
      <div className="leaddash leaddash-page"><div className="page-wrap">
        <div className="back-link" onClick={() => navigate(backToOverview)}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} /> Packs
        </div>
        <div className="reject-banner">{err}</div>
      </div></div>
    );
  }
  if (!plan || !status) return null;

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="back-link" onClick={() => navigate(backToOverview)}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} />
          Packs / <span>{plan.title}</span>
        </div>

        <div className="tl-head">
          <div>
            <h1>{plan.title}</h1>
            <p>
              {plan.period_label} close
              {plan.due_date ? ` · Due ${fmtDate(plan.due_date, bsLabels)}` : ''}
            </p>
          </div>
          {editable && (
            <button className="btn btn-primary" onClick={() => setAddOpen(true)}>+ Add Task</button>
          )}
        </div>

        {!editable && (
          <div className="readonly-banner">
            View only — this pack is
            {plan.owner ? ` assigned to ${plan.owner.name}` : ' not assigned to a Team Lead yet'}.
            You can review its tasks but cannot make changes.
          </div>
        )}
        {err && <div className="reject-banner">{err}</div>}
        {seriesFlash && <div className="readonly-banner">{seriesFlash}</div>}

        <div className="pack-info-card">
          <div className="pack-info-top">
            <div className="pack-info-desc">{plan.description || 'No description.'}</div>
            <span className={`status-tag ${status.tag}`}>{status.label}</span>
          </div>

          <ProgressBar
            pct={stats.pct}
            fillStyle={
              status.key === 'risk' ? 'linear-gradient(90deg,#E5484D,#F0817F)'
                : status.key === 'closed' ? 'linear-gradient(90deg,#1FA971,#3ECF8E)'
                  : undefined
            }
          />

          <div className="pack-meta-row">
            <div className="pack-meta-item"><div className="k">Progress</div><div className="v">{stats.pct}%</div></div>
            <div className="pack-meta-item"><div className="k">Tasks completed</div><div className="v">{stats.completed}<span className="v-sub">/ {stats.total}</span></div></div>
            <div className="pack-meta-item"><div className="k">Pending review</div><div className="v">{stats.review}</div></div>
          </div>
        </div>

        <div className="detail-section-block">
          <div className="section-title">Assigned Team Lead</div>
          {plan.owner ? (
            <div className="lead-pill">
              <Avatar initials={initialsOf(plan.owner.name)} color={colorFor(plan.owner.id)} />
              <div>
                <div className="assignee-name">{plan.owner.name}</div>
                <div className="assignee-role">Team Lead · responsible for this pack</div>
              </div>
            </div>
          ) : (
            <div className="empty-note">
              Not assigned yet — an Admin needs to assign this pack to a Team Lead.
            </div>
          )}
        </div>

        <div className="detail-section-block">
          <div className="section-title">
            Tasks <span className="section-count">{rows.filter((r) => r.status !== 'skipped').length}</span>
            {rows.some((r) => r.status === 'skipped') && (
              <span className="section-count" title="Skipped tasks (optional, past due, not done) are listed under the Skipped tab and are not counted in the totals">
                + {rows.filter((r) => r.status === 'skipped').length} skipped
              </span>
            )}
            {unassignedCount > 0 && (
              <span className="section-count section-count-warn">{unassignedCount} need assignment</span>
            )}
          </div>

          <div className="tasklist-toolbar">
            <StatusTabs tabs={tabs} active={statusTab} onChange={setStatusTab} />
            <div className="tasklist-filters">
              <div className="search-box task-search-box">
                <Icon name="search" size={13} />
                <input
                  placeholder="Search tasks"
                  value={taskSearch}
                  onChange={(e) => setTaskSearch(e.target.value)}
                />
              </div>
              {allTags.length > 0 && (
                <label className="tag-filter-select">
                  <Icon name="layers" size={13} />
                  <select value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
                    <option value="">All tags</option>
                    {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
              )}
              {(tagFilter || taskSearch) && (
                <button
                  type="button"
                  className="clear-filters"
                  onClick={() => { setTagFilter(''); setTaskSearch(''); }}
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          <div className="table-card">
            <AllTasksTable
              rows={tabRows}
              resetKey={`${statusTab}|${tagFilter}`}
              readOnly={!editable}
              members={members}
              onOpenDetail={openDetail}
              onReview={reviewRow}
              onApprove={approveRow}
              onEdit={(r) => setEditTask(r)}
              onEditSubtasks={(r) => setEditSubtasksTask(r.raw)}
              onDelete={deleteTask}
              onAssignEntity={assignEntity}
              onAssignMulti={assignMulti}
              onAssignSeries={assignSeries}
              onComplete={completeRow}
              onReopen={reopenRow}
              onBulkAssign={bulkAssign}
              onBulkReview={bulkReview}
            />
          </div>
        </div>

        <div className="detail-section-block">
          <div className="section-title">
            Team / Team Members <span className="section-count">{team.length}</span>
          </div>
          {team.length === 0 ? (
            <div className="empty-note">No team members assigned to this pack's tasks yet.</div>
          ) : (
            <div className="team-grid">
              {team.map((member) => (
                <div className="team-member-card" key={member.id}>
                  <Avatar initials={member.initials} color={member.color} />
                  <div>
                    <div className="assignee-name">{member.name}</div>
                    <div className="assignee-role">
                      {member.role === 'TEAM_LEAD' ? 'Team Lead' : 'Team Member'}
                    </div>
                  </div>
                  <span className="tm-count">
                    {member.count} {member.count === 1 ? 'task' : 'tasks'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {addOpen && (
        <AddTaskModal
          planId={plan.id}
          planDueDate={plan.due_date}
          periodStart={plan.period_start_date}
          periodEnd={plan.period_end_date || plan.due_date}
          calendarType={plan.calendar_type}
          members={members}
          onClose={() => setAddOpen(false)}
          onCreated={() => { setAddOpen(false); load(); }}
        />
      )}

      {editTask && (
        <EditTaskModal
          task={editTask.raw}
          seriesSize={editTask.seriesSize}
          members={members}
          periodStart={plan.period_start_date}
          periodEnd={plan.period_end_date || plan.due_date}
          calendarType={plan.calendar_type}
          onClose={() => setEditTask(null)}
          onSaved={() => { setEditTask(null); load(); }}
        />
      )}

      {editSubtasksTask && (
        <EditSubtasksModal
          task={editSubtasksTask}
          seriesCounts={subtaskSeriesCounts}
          members={members}
          periodStart={plan.period_start_date}
          periodEnd={plan.period_end_date || plan.due_date}
          calendarType={plan.calendar_type}
          onClose={() => setEditSubtasksTask(null)}
          onSaved={() => { setEditSubtasksTask(null); load(); }}
        />
      )}

      {reviewItem && (
        <ReviewModal
          item={reviewItem}
          onClose={() => setReviewItem(null)}
          onDone={() => { setReviewItem(null); load(); }}
        />
      )}

      {completeItem && (
        <SubmitTaskModal
          item={completeItem}
          // A Lead completing their OWN solo item skips the separate review
          // step, so Notes + Time are mandatory (matches
          // ensure_completion_details on the backend).
          required
          entityWide={completeItem.entityWide}
          onClose={() => setCompleteItem(null)}
          onDone={() => { setCompleteItem(null); load(); }}
        />
      )}

      {detailTaskId && (
        <TaskDetailPanel
          taskId={detailTaskId}
          members={members}
          readOnly={!editable}
          periodStart={plan.period_start_date}
          periodEnd={plan.period_end_date || plan.due_date}
          calendarType={plan.calendar_type}
          bsLabels={bsLabels}
          onClose={() => setDetailTaskId(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}
