// Team Lead / Admin — every task & sub-task assigned to one team member,
// for a chosen period (the current one, or a past / closed period for a
// historical summary). Opened by clicking a person in the dashboard's
// "Team Workload" panel.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { dashboardAPI, periodAPI, userAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import Avatar from '../components/ui/Avatar';
import Badge from '../components/ui/Badge';
import { ProgressBar } from '../components/ui/ProgressBar';
import StatusTabs from '../components/tasklist/StatusTabs';
import {
  memberLeafItems, memberStatusKey, fmtDate, dueMeta,
  initialsOf, colorFor, STATUS_TABS, useBsDateLabels, periodDropdownTag, inStatusTab,
} from './leadDashUtils';

export default function MemberTasksView() {
  const { userId } = useParams();
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const [searchParams, setSearchParams] = useSearchParams();
  const periodId = searchParams.get('period') || '';

  const [member, setMember] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [work, setWork] = useState(null);
  const [err, setErr] = useState('');
  const [statusTab, setStatusTab] = useState('');

  const load = useCallback(async () => {
    const params = periodId ? { period_id: periodId } : {};
    const [usersR, perR, workR] = await Promise.allSettled([
      userAPI.list(),
      periodAPI.list(),
      dashboardAPI.memberPeriodWork(userId, params),
    ]);
    if (usersR.status === 'fulfilled') {
      setMember(usersR.value.data.find((u) => u.id === userId) || null);
    }
    if (perR.status === 'fulfilled') setPeriods(perR.value.data);
    if (workR.status === 'fulfilled') {
      setWork(workR.value.data);
      setErr('');
    } else {
      setErr(getErrorMessage(workR.reason, 'Could not load this member’s tasks.'));
    }
  }, [userId, periodId]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  // Leaf work items (a task with sub-tasks contributes its sub-tasks),
  // deduped by id — same shape MemberDashboard uses.
  const items = useMemo(
    () => memberLeafItems(work, userId).map((it) => ({ ...it, _key: memberStatusKey(it) })),
    [work, userId],
  );

  // Items may span multiple packs of different calendar_type (now stamped
  // per item by /me/period-work) — same reasoning as MemberDashboard's
  // bsItemDates: scope the BS-label batch to just the BS items' own dates.
  const bsItemDates = useMemo(
    () => items.filter((it) => it.calendar_type === 'BS' && it.due_date).map((it) => it.due_date),
    [items],
  );
  const bsItemLabels = useBsDateLabels(bsItemDates, 'BS');

  const summary = useMemo(() => {
    // Skipped work (optional, past due, not done) is listed but never counted.
    const counted = items.filter((i) => i._key !== 'skipped');
    const total = counted.length;
    const completed = counted.filter((i) => i.status === 'COMPLETED').length;
    const review = counted.filter((i) => i.status === 'PENDING_REVIEW').length;
    const overdue = counted.filter((i) => i._key === 'overdue').length;
    return {
      total, completed, review, overdue,
      pct: total ? Math.round((completed / total) * 100) : 0,
    };
  }, [items]);

  const tabs = STATUS_TABS.map((t) => ({
    status: t.status,
    label: t.label,
    count: items.filter((i) => inStatusTab(i._key, t.status)).length,
  }));

  const shown = items.filter((i) => inStatusTab(i._key, statusTab));
  const skippedCount = items.filter((i) => i._key === 'skipped').length;

  const activePeriodId = periodId || work?.period_id || '';
  const activePeriod = periods.find((p) => p.id === activePeriodId) || null;
  const activePeriodLabel = activePeriod?.label || work?.period_label || '';
  const isPast = !!periodId && activePeriod?.status === 'CLOSED';

  const setPeriod = (id) => setSearchParams(id ? { period: id } : {});

  const memberName = member?.name || 'Team Member';

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="back-link" onClick={() => navigate(orgPath(
          `/${periodId ? `?period=${periodId}` : ''}`,
        ))}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} /> Overview
        </div>

        <div className="tl-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <Avatar
              initials={initialsOf(memberName)}
              color={colorFor(userId)}
              style={{ width: 46, height: 46, fontSize: 15 }}
            />
            <div>
              <h1>{memberName}</h1>
              <p>
                {member?.email ? `${member.email} · ` : ''}
                Assigned tasks{activePeriodLabel ? ` · ${activePeriodLabel}` : ''}
              </p>
            </div>
          </div>

          {periods.length > 0 && (
            <label className="dd-filter period-select">
              <Icon name="calendar" size={14} />
              <select value={periodId} onChange={(e) => setPeriod(e.target.value)}>
                <option value="">Current period</option>
                {[...periods].reverse().map((p) => {
                  const tag = periodDropdownTag(p);
                  return (
                    <option key={p.id} value={p.id}>
                      {p.label}{tag ? ` (${tag})` : ''}
                    </option>
                  );
                })}
              </select>
            </label>
          )}
        </div>

        {err && <div className="reject-banner">{err}</div>}
        {isPast && (
          <div className="readonly-banner">
            Viewing a past period — a read-only summary of {memberName}&apos;s work in {activePeriodLabel}.
          </div>
        )}

        <div className="pack-info-card">
          <div className="pack-info-top">
            <div className="pack-info-desc">
              {summary.total === 0
                ? 'No tasks were assigned to this member in this period.'
                : `${summary.completed} of ${summary.total} completed`}
            </div>
          </div>
          <ProgressBar pct={summary.pct} />
          <div className="pack-meta-row">
            <div className="pack-meta-item"><div className="k">Total tasks</div><div className="v">{summary.total}</div></div>
            <div className="pack-meta-item"><div className="k">Completed</div><div className="v">{summary.completed}</div></div>
            <div className="pack-meta-item"><div className="k">Pending review</div><div className="v">{summary.review}</div></div>
            <div className="pack-meta-item"><div className="k">Overdue</div><div className="v">{summary.overdue}</div></div>
          </div>
        </div>

        <div className="detail-section-block">
          <div className="section-title">
            Tasks <span className="section-count">{items.length - skippedCount}</span>
            {skippedCount > 0 && (
              <span className="section-count" title="Skipped tasks (optional, past due, not done) are listed under the Skipped tab and are not counted in the totals">
                + {skippedCount} skipped
              </span>
            )}
          </div>

          {items.length === 0 ? (
            <div className="empty-note">Nothing assigned in this period.</div>
          ) : (
            <>
              <div className="tasklist-toolbar">
                <StatusTabs tabs={tabs} active={statusTab} onChange={setStatusTab} />
              </div>
              <div className="table-card">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 44 }}>SN</th>
                      <th>Task</th>
                      <th>Type</th>
                      <th>Entity</th>
                      <th>Due Date</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.length === 0 ? (
                      <tr>
                        <td colSpan={6} style={{ textAlign: 'center', color: 'var(--gray-mute)', padding: 28 }}>
                          No tasks in this view.
                        </td>
                      </tr>
                    ) : shown.map((it, i) => (
                      <tr key={it.id ?? i}>
                        <td className="sn-cell">{i + 1}</td>
                        <td className="task-cell">
                          <div className="task-title">{it.title}</div>
                          {it.description && <div className="task-sub">{it.description}</div>}
                        </td>
                        <td>{it._kind === 'SUBTASK' ? 'Sub-task' : 'Task'}</td>
                        <td>{it.entity_name || '—'}</td>
                        <td className="due-cell">
                          <div className="dd">
                            {it.due_date
                              ? fmtDate(it.due_date, it.calendar_type === 'BS' ? bsItemLabels : undefined)
                              : '—'}
                          </div>
                          <div className="dleft">
                            {dueMeta(it.due_date, it._key, it.calendar_type === 'BS' ? bsItemLabels : undefined)}
                          </div>
                        </td>
                        <td><Badge status={it._key} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
