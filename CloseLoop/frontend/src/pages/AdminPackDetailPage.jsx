// Super Admin only — a pack opened from the Admin Dashboard: description,
// whole-pack Team Lead assignment, and its tasks with the SAME task
// management the Team Lead gets in their own pack workspace (open a task's
// detail, edit a task/its subtasks, assign to specific people or the whole
// entity, apply an assignment across a recurring series, review/approve
// submissions, delete a custom task). The backend already allows an admin
// to structurally edit any plan (`can_lead_edit_plan` — see
// plan_customization_service.py), regardless of who owns the pack, so this
// page no longer needs to stay read-only.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { planAPI, userAPI, taskAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import Icon from '../components/ui/Icon';
import StatusTabs from '../components/tasklist/StatusTabs';
import AllTasksTable from '../components/alltasks/AllTasksTable';
import EditTaskModal from '../components/modals/EditTaskModal';
import EditSubtasksModal from '../components/modals/EditSubtasksModal';
import ReviewModal from '../components/modals/ReviewModal';
import SubmitTaskModal from '../components/member/SubmitTaskModal';
import TaskDetailPanel from '../components/detail/TaskDetailPanel';
import {
  buildPlanTaskRows, buildSubtaskSeriesCounts, collectPlanDates, useBsDateLabels,
  fmtDate, packStatus, STATUS_TABS, inStatusTab,
} from './leadDashUtils';

export default function AdminPackDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const { user } = useAuth();

  const [plan, setPlan] = useState(null);
  const [leads, setLeads] = useState([]);
  const [members, setMembers] = useState([]);
  const [err, setErr] = useState('');
  const [savingOwner, setSavingOwner] = useState(false);
  const [seriesFlash, setSeriesFlash] = useState('');
  const [statusTab, setStatusTab] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [editTask, setEditTask] = useState(null);
  const [editSubtasksTask, setEditSubtasksTask] = useState(null);
  const [detailTaskId, setDetailTaskId] = useState(null);
  const [reviewItem, setReviewItem] = useState(null);
  const [completeItem, setCompleteItem] = useState(null);

  const load = useCallback(async () => {
    try {
      const [planR, usersR] = await Promise.all([planAPI.get(id), userAPI.list()]);
      setPlan(planR.data);
      setLeads(usersR.data.filter((u) => u.role === 'TEAM_LEAD'));
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

  // This pack's own dates rendered in whichever calendar the pack itself
  // is configured for — a BS pack shows BS everywhere here, an AD pack is
  // untouched.
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
  const subtaskSeriesCounts = useMemo(
    () => buildSubtaskSeriesCounts(plan?.tasks || []),
    [plan],
  );

  const stats = useMemo(() => {
    // Skipped tasks (optional, past due, not done) are listed but never counted.
    const workRows = rows.filter((r) => !r.isScheduled && r.status !== 'skipped');
    const total = workRows.length;
    const completed = workRows.filter((r) => r.status === 'completed').length;
    return {
      total,
      completed,
      review: workRows.filter((r) => r.status === 'review').length,
      overdue: workRows.filter((r) => r.status === 'overdue').length,
      pct: Math.round(plan?.progress ?? (total ? (completed / total) * 100 : 0)),
    };
  }, [rows, plan]);

  const status = plan ? packStatus(plan, stats) : null;

  const tabs = STATUS_TABS.filter((t) => t.status !== 'upcoming').map((t) => ({
    status: t.status,
    label: t.label,
    count: rows.filter((r) => inStatusTab(r.status, t.status)).length,
  }));
  const allTags = useMemo(
    () => [...new Set(rows.flatMap((r) => r.tags || []))].sort((a, b) => a.localeCompare(b)),
    [rows],
  );
  const tabRows = useMemo(
    () => rows
      .filter((r) => inStatusTab(r.status, statusTab))
      .filter((r) => !tagFilter || (r.tags || []).includes(tagFilter)),
    [rows, statusTab, tagFilter],
  );

  const assignOwner = async (ownerId) => {
    setSavingOwner(true);
    try {
      await planAPI.assignOwner(id, ownerId || null);
      await load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not update the owning Team Lead.'));
    } finally {
      setSavingOwner(false);
    }
  };

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

  const completeRow = (row) => {
    setCompleteItem({ id: row.id, kind: 'TASK', title: row.title, entityWide: !!row.completeViaEntity });
  };

  // Un-approve a completed leaf task — flip it back to Pending Review so
  // it can be rejected (sending it back to the assignee to redo and
  // resubmit) or approved again.
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

  const openDetail = (row) => setDetailTaskId(row.id);

  if (err && !plan) {
    return (
      <div className="leaddash leaddash-page"><div className="page-wrap">
        <div className="back-link" onClick={() => navigate(orgPath('/'))}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} /> Back to dashboard
        </div>
        <div className="reject-banner">{err}</div>
      </div></div>
    );
  }
  if (!plan || !status) return null;

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
      <div className="back-link" onClick={() => navigate(orgPath('/'))}>
        <Icon name="chevron-left" size={13} strokeWidth={2.4} />
        Back to dashboard
      </div>

      {err && <div className="reject-banner">{err}</div>}
      {seriesFlash && <div className="readonly-banner">{seriesFlash}</div>}

      <div className="pack-info-card">
        <div className="pack-info-top">
          <div>
            <h1>{plan.title}</h1>
            <div className="admin-pack-badges">
              {plan.period_label && <span className="pack-badge">{plan.period_label}</span>}
              {plan.entity_name && <span className="pack-badge code" style={{ background: '#2F55D4' }}>{plan.entity_name}</span>}
              {plan.due_date && <span className="pack-badge-due">Due {fmtDate(plan.due_date, bsLabels)}</span>}
            </div>
          </div>

          <div className="admin-pack-status-col">
            <span className={`status-tag ${status.tag}`}>{status.label}</span>
            <div className="admin-pack-progress">
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${stats.pct}%` }} />
              </div>
              <span>{stats.completed} / {stats.total} tasks</span>
            </div>
          </div>
        </div>

        <div className="pack-info-desc">{plan.description || 'No description.'}</div>

        <div className="form-group admin-assign-whole">
          <label>Assign whole pack to</label>
          <select
            className="field"
            value={plan.owner_id || ''}
            disabled={savingOwner}
            onChange={(e) => assignOwner(e.target.value)}
          >
            <option value="">Unassigned</option>
            {leads.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </div>
      </div>

      <div className="detail-section-block">
        <div className="section-title">
          Tasks <span className="section-count">{rows.filter((r) => r.status !== 'skipped').length}</span>
          {rows.some((r) => r.status === 'skipped') && (
            <span className="section-count" title="Skipped tasks (optional, past due, not done) are listed under the Skipped tab and are not counted in the totals">
              + {rows.filter((r) => r.status === 'skipped').length} skipped
            </span>
          )}
        </div>

        <div className="tasklist-toolbar">
          <StatusTabs tabs={tabs} active={statusTab} onChange={setStatusTab} />
          {allTags.length > 0 && (
            <div className="tasklist-filters">
              <label className="tag-filter-select">
                <Icon name="layers" size={13} />
                <select value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
                  <option value="">All tags</option>
                  {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              {tagFilter && (
                <button type="button" className="clear-filters" onClick={() => setTagFilter('')}>
                  Clear
                </button>
              )}
            </div>
          )}
        </div>

        {rows.length === 0 ? (
          <div className="empty-note">No tasks in this pack yet.</div>
        ) : (
          <div className="table-card">
            <AllTasksTable
              rows={tabRows}
              resetKey={`${statusTab}|${tagFilter}`}
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
            />
          </div>
        )}
      </div>
      </div>

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
