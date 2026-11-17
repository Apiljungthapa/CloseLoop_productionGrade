// Team Member pack view (/my-packs/:id) — the member's own tasks inside one
// pack, with a "Submit for review" action. Design ported from Frontend_01.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { teamAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import Avatar from '../components/ui/Avatar';
import { ProgressBar } from '../components/ui/ProgressBar';
import StatusTabs from '../components/tasklist/StatusTabs';
import AllTasksTable from '../components/alltasks/AllTasksTable';
import SubmitTaskModal from '../components/member/SubmitTaskModal';
import MemberTaskPanel from '../components/member/MemberTaskPanel';
import {
  memberStatusKey, PRIORITY_TO_KEY, personFromUser, personsForSubtask, taskAssignees,
  fmtDate, dueMeta, STATUS_TABS, collectPlanDates, useBsDateLabels, dueWeekBucket,
  inStatusTab, memberPackWork, memberPackStats,
} from './leadDashUtils';

const SCOPES = ['Assigned to Me', 'All Tasks'];

export default function MemberPackView() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const { user } = useAuth();

  const [plan, setPlan] = useState(null);
  const [err, setErr] = useState('');
  const [statusTab, setStatusTab] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [scope, setScope] = useState('Assigned to Me');
  const [submitItem, setSubmitItem] = useState(null);
  const [detailItem, setDetailItem] = useState(null);
  // Bumped after a submit so the open task overview re-fetches itself.
  const [panelRefresh, setPanelRefresh] = useState(0);

  const load = useCallback(async () => {
    try {
      const { data } = await teamAPI.packDetail(id);
      setPlan(data);
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not load this pack.'));
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  // This pack's own dates rendered in whichever calendar the pack itself
  // is configured for — a BS pack shows BS everywhere here, an AD pack is
  // untouched.
  const planDates = useMemo(
    () => (plan?.due_date ? [plan.due_date] : []).concat(collectPlanDates(plan?.tasks || [])),
    [plan],
  );
  const bsLabels = useBsDateLabels(planDates, plan?.calendar_type);

  // ---- the member's own work in this pack, and its progress ----
  // Shared with the pack card on the dashboard (leadDashUtils) so both always agree.
  const myWork = useMemo(() => memberPackWork(plan?.tasks, user.id), [plan, user.id]);
  const myStats = useMemo(() => memberPackStats(myWork, user.id), [myWork, user.id]);

  // ---- rows for the table ----
  // Rolled-up status for a parent task, from its (my) sub-tasks — drives
  // which status tab the parent row shows under.
  const rollUp = (keys) => (
    ['rejected', 'overdue', 'duetoday', 'progress', 'notstarted', 'skipped', 'review', 'completed', 'upcoming']
      .find((k) => keys.includes(k)) || 'notstarted'
  );

  const mineRows = useMemo(() => myWork.map((w) => {
    if (w.kind === 'TASK_WITH_SUBS') {
      const t = w.task;
      const subViews = w.mySubs.map((s) => {
        const myRow = (s.entity_assignees || []).find((a) => a.user_id === user.id);
        const shared = !!s.assigned_entity_wide;
        // My own part done but the whole sub-task not yet submitted/approved
        // (waiting on the other assignees) — show it like "submitted".
        const myPartWaiting = shared && myRow?.done
          && s.status !== 'PENDING_REVIEW' && s.status !== 'COMPLETED';
        const skey = myPartWaiting ? 'review' : memberStatusKey({ ...s, is_required: t.is_required });
        // My own deadline on a shared sub-task, if the Lead gave me one —
        // falls back to the sub-task's own due date when they didn't (see
        // SubTaskEntityAssignee.due_date). A non-shared sub-task has just
        // the one due date, so this is a no-op there.
        const myDue = (shared && myRow?.due_date) || s.due_date;
        return {
          id: s.id,
          kind: 'SUBTASK',
          name: s.title,
          title: s.title,
          desc: s.description || '',
          entityWide: shared,
          assignees: personsForSubtask(s),
          due: myDue ? fmtDate(myDue, bsLabels) : '',
          dueDateRaw: myDue || null,
          status: skey,
          priority: PRIORITY_TO_KEY[s.priority] || 'med',
          // Not actionable yet — locked until its real occurrence arrives.
          canSubmit: s.status !== 'UPCOMING' && (shared
            ? (!myRow?.done && s.status !== 'COMPLETED')
            : (s.status !== 'PENDING_REVIEW' && s.status !== 'COMPLETED')),
          canWithdraw: !shared && s.status === 'PENDING_REVIEW',
        };
      });
      const doneCount = w.mySubs.filter((s) => {
        if (s.assigned_entity_wide) {
          const r = (s.entity_assignees || []).find((a) => a.user_id === user.id);
          return s.status === 'COMPLETED' || !!r?.done;
        }
        return s.status === 'COMPLETED';
      }).length;
      const rolled = rollUp(subViews.map((s) => s.status));
      // Same aggregation the Lead's table uses for a container task: since
      // the task itself never carries a direct assignee once it has
      // sub-tasks, show the union of every (non-upcoming) sub-task's
      // assignees — not just the caller's own — so the row matches what
      // the Lead sees instead of reading as unassigned.
      const allSubs = (t.subtasks || []).filter((s) => s.status !== 'UPCOMING');
      return {
        id: t.id,
        kind: 'TASK',
        parentOfMine: true,
        title: t.title,
        sub: `${w.mySubs.length} subtask${w.mySubs.length > 1 ? 's' : ''} assigned to you`,
        desc: t.description || '',
        assignees: taskAssignees(t, allSubs),
        due: t.due_date ? fmtDate(t.due_date, bsLabels) : '—',
        dueDateRaw: t.due_date || null,
        dmeta: dueMeta(t.due_date, rolled, bsLabels),
        status: rolled,
        priority: PRIORITY_TO_KEY[t.priority] || 'med',
        tags: t.tags || [],
        prog: [doneCount, w.mySubs.length],
        subtasks: subViews,
        canSubmit: false,
      };
    }
    const it = w.item;
    // Once the whole task is COMPLETED it must read as completed for
    // everyone — the "my part done, waiting on others" (review) state only
    // applies while the task itself is still open.
    const submittedMine = w.entityWide
      ? w.myRow.done && it.status !== 'PENDING_REVIEW' && it.status !== 'COMPLETED'
      : false;
    const key = w.entityWide && w.myRow.done
      && it.status !== 'PENDING_REVIEW' && it.status !== 'COMPLETED'
      ? 'review' : memberStatusKey(it);
    // Not actionable yet — locked until its real occurrence arrives.
    const actionable = it.status !== 'UPCOMING' && (w.entityWide
      ? !w.myRow.done && it.status !== 'COMPLETED'
      : it.status !== 'PENDING_REVIEW' && it.status !== 'COMPLETED');
    // A plain (non-shared) submission the Lead hasn't reviewed yet can be
    // pulled back to keep working on — toggle it off to withdraw.
    const canWithdraw = !w.entityWide && it.status === 'PENDING_REVIEW';
    // My own deadline on a shared (whole-entity) task, if the Lead gave me
    // one — falls back to the task's own due date otherwise. A plain
    // single-assignee task has just the one due date, so this is a no-op.
    const myDue = (w.entityWide && w.myRow?.due_date) || it.due_date;
    return {
      id: it.id,
      kind: w.kind,
      entityWide: !!w.entityWide,
      title: it.title,
      sub: w.entityWide ? 'Whole entity' : it.status === 'UPCOMING' ? 'Upcoming period — locked' : '',
      desc: it.description || '',
      assignees: taskAssignees(it, []),
      due: myDue ? fmtDate(myDue, bsLabels) : '—',
      dueDateRaw: myDue || null,
      dmeta: dueMeta(myDue, key, bsLabels),
      status: key,
      priority: PRIORITY_TO_KEY[it.priority] || 'med',
      tags: it.tags || [],
      prog: [0, 0],
      subtasks: [],
      canSubmit: actionable,
      canWithdraw,
      _submitted: submittedMine,
    };
  }), [myWork, user, bsLabels]);

  const allRows = useMemo(() => (plan?.tasks || []).map((t) => {
    const subs = t.subtasks || [];
    const done = subs.filter((s) => s.status === 'COMPLETED').length;
    return {
      id: t.id,
      title: t.title,
      sub: t.carried_from_label
        ? `Carried forward from ${t.carried_from_label}`
        : t.status === 'UPCOMING' ? 'Upcoming period'
          : (t.is_custom ? 'Custom task' : ''),
      desc: t.description || '',
      assignees: taskAssignees(t, subs),
      due: t.due_date ? fmtDate(t.due_date, bsLabels) : '—',
      dueDateRaw: t.due_date || null,
      dmeta: dueMeta(t.due_date, memberStatusKey(t), bsLabels),
      status: memberStatusKey(t),
      priority: PRIORITY_TO_KEY[t.priority] || 'med',
      tags: t.tags || [],
      prog: [done, subs.length],
      subtasks: subs.map((s) => ({
        id: s.id, name: s.title, desc: s.description || '',
        assignees: personsForSubtask(s),
        due: s.due_date ? fmtDate(s.due_date, bsLabels) : '',
        dueDateRaw: s.due_date || null,
      })),
      canSubmit: false,
    };
  }), [plan, bsLabels]);

  const rows = useMemo(
    () => (scope === 'All Tasks' ? allRows : mineRows),
    [scope, allRows, mineRows],
  );

  // Deep link from elsewhere (e.g. the dashboard's deadline tables):
  // /my-packs/:id?task=<id> opens that task/sub-task's detail panel as soon
  // as its row is available (same construction onOpenDetail below uses),
  // and switches to that task's own status tab so the table underneath
  // lands on the same "page" the task actually lives on instead of
  // whatever tab happened to be active.
  // Carried through from MemberDashboard so "Overview" below returns to
  // whichever period the member was viewing rather than the current one.
  const periodParam = searchParams.get('period') || '';
  const backToOverview = orgPath(`/${periodParam ? `?period=${periodParam}` : ''}`);

  const openTaskId = searchParams.get('task');
  useEffect(() => {
    if (!openTaskId) return;
    const findIn = (list) => list.find((x) => x.id === openTaskId)
      || list.flatMap((x) => x.subtasks || []).find((x) => x.id === openTaskId);
    let r = findIn(rows);
    // Not in the current scope (e.g. an entity-wide task not directly
    // "mine") — fall back to the full task list rather than silently
    // failing to open.
    if (!r && scope !== 'All Tasks') {
      r = findIn(allRows);
      if (r) setScope('All Tasks');
    }
    if (!r) return;
    setStatusTab(r.status);
    setDetailItem({
      id: r.id, kind: r.kind || 'TASK', title: r.title, canSubmit: !!r.canSubmit,
      canWithdraw: !!r.canWithdraw, entityWide: !!r.entityWide,
    });
    setSearchParams((p) => {
      const n = new URLSearchParams(p);
      n.delete('task');
      return n;
    }, { replace: true });
  }, [openTaskId, rows, allRows, scope]);

  const allTags = useMemo(
    () => [...new Set(rows.flatMap((r) => r.tags || []))].sort((a, b) => a.localeCompare(b)),
    [rows],
  );
  // "Due This Week" / "Due Next Week" — same buckets as the Lead workspace:
  // only not-started/in-progress rows count (today's live in "Due Today",
  // past ones in "Overdue"), so the tabs never overlap.
  const weekBucketOf = useCallback((row) => {
    if (row.status !== 'notstarted' && row.status !== 'progress') return null;
    return dueWeekBucket(row.dueDateRaw);
  }, []);
  const matchesTab = useCallback((r, tab) => {
    // Skipped work lives under the Skipped tab only (see inStatusTab).
    if (tab === 'duethisweek') return weekBucketOf(r) === 'thisweek';
    if (tab === 'duenextweek') return weekBucketOf(r) === 'nextweek';
    return inStatusTab(r.status, tab);
  }, [weekBucketOf]);
  const tabRows = useMemo(
    () => {
      const out = rows
        .filter((r) => matchesTab(r, statusTab))
        .filter((r) => !tagFilter || (r.tags || []).includes(tagFilter));
      if (statusTab === 'duethisweek' || statusTab === 'duenextweek') {
        const rank = { high: 0, med: 1, low: 2 };
        out.sort((a, b) => (
          (a.dueDateRaw || '9999-99-99').localeCompare(b.dueDateRaw || '9999-99-99')
          || (rank[a.priority] ?? 9) - (rank[b.priority] ?? 9)
          || (a.title || '').localeCompare(b.title || '')
        ));
      }
      return out;
    },
    [rows, statusTab, tagFilter, matchesTab],
  );
  const tabs = STATUS_TABS.flatMap((tb) => {
    const tab = {
      status: tb.status,
      label: tb.label,
      count: rows.filter((r) => matchesTab(r, tb.status)).length,
    };
    return tb.status === 'duetoday'
      ? [
        tab,
        { status: 'duethisweek', label: 'Due This Week', count: rows.filter((r) => matchesTab(r, 'duethisweek')).length },
        { status: 'duenextweek', label: 'Due Next Week', count: rows.filter((r) => matchesTab(r, 'duenextweek')).length },
      ]
      : [tab];
  });

  const lead = plan?.owner ? personFromUser(plan.owner) : null;

  // A shared (whole-entity / multi-assign) task requires Time Taken and
  // Notes — every assignee on it leaves the same accountability trail.
  // A plain task assigned to just this member keeps them optional.
  const onRowSubmit = (row) => {
    setSubmitItem({
      id: row.id, kind: row.kind, title: row.title,
      entityWide: !!row.entityWide,
    });
  };

  // Toggle-off on a submitted task — pull it back to In Progress so the
  // member can keep working and resubmit. Their earlier notes/time stay.
  const withdrawRow = async (row) => {
    try {
      await teamAPI.setStatus(row.id, { status: 'IN_PROGRESS' });
      load();
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not withdraw this submission.'));
    }
  };

  if (err && !plan) {
    return (
      <div className="leaddash leaddash-page"><div className="page-wrap">
        <div className="back-link" onClick={() => navigate(backToOverview)}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} /> Overview
        </div>
        <div className="reject-banner">{err}</div>
      </div></div>
    );
  }
  if (!plan) return null;

  const infoTag = myStats.total === 0
    ? { label: 'No tasks assigned to you', cls: 'view-only-tag' }
    : myStats.completed === myStats.total
      ? { label: 'All your tasks done', cls: 'status-closed' }
      : { label: 'In progress', cls: 'status-open' };

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="back-link" onClick={() => navigate(backToOverview)}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} />
          Overview / <span>{plan.title}</span>
        </div>

        <div className="tl-head">
          <div>
            <h1>{plan.title}</h1>
            <p>{plan.period_label} close{plan.due_date ? ` · Due ${fmtDate(plan.due_date, bsLabels)}` : ''}</p>
          </div>
        </div>

        <div className="readonly-banner">
          You can open and complete the tasks assigned to you here. Task assignment is managed by your Team Lead.
        </div>
        {err && <div className="reject-banner">{err}</div>}

        <div className="pack-info-card">
          <div className="pack-info-top">
            <div className="pack-info-desc">{plan.description || 'No description.'}</div>
            <span className={`status-tag ${infoTag.cls}`}>{infoTag.label}</span>
          </div>
          <ProgressBar
            pct={myStats.pct}
            fillStyle={myStats.completed === myStats.total && myStats.total > 0
              ? 'linear-gradient(90deg,#1FA971,#3ECF8E)' : undefined}
          />
          <div className="pack-meta-row">
            <div className="pack-meta-item"><div className="k">Progress</div><div className="v">{myStats.pct}%</div></div>
            <div className="pack-meta-item"><div className="k">My tasks</div><div className="v">{myStats.total}</div></div>
            <div className="pack-meta-item"><div className="k">Completed</div><div className="v">{myStats.completed}</div></div>
            <div className="pack-meta-item"><div className="k">Submitted for review</div><div className="v">{myStats.review}</div></div>
          </div>
        </div>

        <div className="detail-section-block">
          <div className="section-title">Assigned Team Lead</div>
          {lead ? (
            <div className="lead-pill">
              <Avatar initials={lead.initials} color={lead.color} />
              <div>
                <div className="assignee-name">{lead.name}</div>
                <div className="assignee-role">Team Lead · responsible for this pack</div>
              </div>
            </div>
          ) : (
            <div className="empty-note">Not assigned yet.</div>
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
          </div>

          <div className="filter-row" style={{ marginBottom: 14 }}>
            {SCOPES.map((s) => (
              <button
                key={s}
                className={`chip-filter${scope === s ? ' active' : ''}`}
                onClick={() => setScope(s)}
              >
                {s}
              </button>
            ))}
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

          <div className="table-card">
            <AllTasksTable
              rows={tabRows}
              resetKey={`${statusTab}|${tagFilter}`}
              mode="member"
              meId={user.id}
              onOpenDetail={(r) => {
                setDetailItem({
                  id: r.id, kind: r.kind || 'TASK', title: r.title, canSubmit: !!r.canSubmit,
                  canWithdraw: !!r.canWithdraw, entityWide: !!r.entityWide,
                });
              }}
              onReview={onRowSubmit}
              onWithdraw={withdrawRow}
            />
          </div>
        </div>
      </div>

      {submitItem && (
        <SubmitTaskModal
          item={submitItem}
          required={submitItem.entityWide}
          entityWide={submitItem.entityWide}
          onClose={() => setSubmitItem(null)}
          onDone={() => {
            setSubmitItem(null);
            setPanelRefresh((n) => n + 1);
            load();
          }}
        />
      )}

      {detailItem && (
        <MemberTaskPanel
          item={detailItem}
          canSubmit={detailItem.canSubmit}
          canWithdraw={detailItem.canWithdraw}
          bsLabels={bsLabels}
          refreshSignal={panelRefresh}
          onClose={() => setDetailItem(null)}
          onChanged={load}
          // Submitting a sub-task keeps the parent task overview open
          // underneath (everything lives in that one view); submitting a
          // plain task closes it as before.
          onSubmit={(it) => {
            if (it.kind !== 'SUBTASK') setDetailItem(null);
            setSubmitItem(it);
          }}
          onWithdraw={withdrawRow}
        />
      )}
    </div>
  );
}
