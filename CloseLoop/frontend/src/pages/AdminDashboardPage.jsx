// Super Admin Dashboard — tenant-wide overview: plan counts, every pack,
// and a team headcount summary. Same structure as the Team Lead's Overview
// page (packStatus/stats logic reused from leadDashUtils), scoped to the
// whole tenant rather than "assigned to me". Ported from the Frontend_0323
// design mockup, wired to the real backend.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { planAPI, userAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import Icon from '../components/ui/Icon';
import AdminStatCard from '../components/admin/AdminStatCard';
import AdminPlanRow from '../components/admin/AdminPlanRow';
import AdminTeamPanel from '../components/admin/AdminTeamPanel';
import NewPlanButton from '../components/NewPlanButton.jsx';
import CurrentPeriodBanner from '../components/dashboard/CurrentPeriodBanner.jsx';
import { packStatus, personFromUser, colorFor, fmtDate, useBsDateLabels } from './leadDashUtils';

function entityCode(name) {
  if (!name) return 'PK';
  return String(name).split(/[\s—–-]+/)[0].slice(0, 3).toUpperCase();
}

export default function AdminDashboardPage() {
  const [plans, setPlans] = useState([]);
  const [users, setUsers] = useState([]);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    const [p, u] = await Promise.allSettled([planAPI.list(), userAPI.list()]);
    if (p.status === 'fulfilled') setPlans(p.value.data);
    else setErr(getErrorMessage(p.reason, 'Could not load plans.'));
    if (u.status === 'fulfilled') setUsers(u.value.data);
  }, []);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  // This list mixes packs from potentially different calendar types, so
  // the BS-label batch is scoped to just the BS packs' own due dates — an
  // AD pack's due date must never be looked up in it, even if it happens
  // to share the same calendar date as some BS pack's due date.
  const bsPackDates = useMemo(
    () => plans.filter((p) => p.calendar_type === 'BS' && p.due_date).map((p) => p.due_date),
    [plans],
  );
  const bsLabels = useBsDateLabels(bsPackDates, 'BS');

  const packs = useMemo(() => {
    const today = new Date();
    return plans.map((p) => {
      const overdue = p.due_date && new Date(p.due_date) < today && p.status !== 'CLOSED' ? 1 : 0;
      const stats = {
        total: p.task_count ?? 0,
        completed: p.completed_count ?? 0,
        overdue,
        pct: Math.round(p.progress ?? 0),
        pendingReview: p.pending_review_count ?? 0,
      };
      return {
        pack: {
          id: p.id,
          name: p.title,
          code: entityCode(p.entity_name),
          color: colorFor(p.entity_id),
          period: p.period_label,
          dueDate: p.due_date
            ? fmtDate(p.due_date, p.calendar_type === 'BS' ? bsLabels : undefined)
            : null,
        },
        stats,
        status: packStatus(p, stats),
        lead: personFromUser(p.owner),
      };
    });
  }, [plans, bsLabels]);

  const stats = useMemo(() => ({
    total: packs.length,
    inProgress: packs.filter((m) => m.status.key === 'open').length,
    closed: packs.filter((m) => m.status.key === 'closed').length,
    overdue: packs.filter((m) => m.status.key === 'risk').length,
  }), [packs]);

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
      <div className="page-head">
        <div>
          <div className="admin-title-row">
            <Icon name="shield-check" size={24} style={{ color: 'var(--red)' }} />
            <h1>Super Admin Dashboard</h1>
          </div>
          <p>Tenant-wide visibility · Manage plans, tasks and teams</p>
        </div>
        <div className="head-actions">
          <NewPlanButton onCreated={load} label="Generate Plans" />
        </div>
      </div>

      {err && <div className="reject-banner">{err}</div>}

      <CurrentPeriodBanner refreshKey={plans.length} />

      <div className="admin-stats-grid">
        <AdminStatCard tone="total" icon="layers" label="Total Plans" value={stats.total} />
        <AdminStatCard tone="inprogress" icon="refresh-cw" label="In Progress" value={stats.inProgress} />
        <AdminStatCard tone="closed" icon="check-circle" label="Closed" value={stats.closed} />
        <AdminStatCard tone="overdue" icon="alert-triangle" label="Overdue" value={stats.overdue} />
      </div>

      <div className="admin-body-grid">
        <div className="panel-card">
          <div className="panel-head">
            <h3 className="panel-head-icon-title">
              <Icon name="list" size={15} /> All Plans
            </h3>
          </div>

          <div className="admin-plans-list">
            {packs.length === 0 ? (
              <div className="empty-note">
                No plans yet. Click <b>Generate Plans</b> above to create plans from a Close Pack for one or more entities.
              </div>
            ) : (
              packs.map(({ pack, stats: planStats, status, lead }) => (
                <AdminPlanRow key={pack.id} pack={pack} stats={planStats} status={status} lead={lead} />
              ))
            )}
          </div>
        </div>

        <AdminTeamPanel users={users} />
      </div>
      </div>
    </div>
  );
}
