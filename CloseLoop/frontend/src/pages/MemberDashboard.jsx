// Team Member dashboard — "Overview" scoped to the signed-in member's own work.
// Replaces the old bootstrap TeamMemberDashboard. Design ported from
// Frontend_01, wired to:
//   * teamAPI.dashboard           -> KPI numbers (member-scoped)
//   * dashboardAPI.myPeriodWork   -> deadlines + per-pack progress
//   * teamAPI.packs               -> the packs the member has tasks in
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { teamAPI, dashboardAPI, periodAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import OverviewGrid from '../components/dashboard/OverviewGrid';
import MemberDeadlinesPanel from '../components/dashboard/MemberDeadlinesPanel';
import PackCard from '../components/dashboard/PackCard';
import {
  personFromUser, colorFor, fmtDate, dueMeta, toneForDue,
  memberStatusKey, memberLeafItems, useBsDateLabels, PRIORITY_TO_KEY,
  periodDropdownTag, timeLoggedTodayStats, memberPackWork, memberPackStats,
} from './leadDashUtils';

function entityCode(name) {
  if (!name) return 'PK';
  return String(name).split(/[\s—–-]+/)[0].slice(0, 3).toUpperCase();
}

export default function MemberDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const orgPath = useOrgPath();

  // The selected period lives in the URL (not just component state) so it
  // survives a page refresh and a round-trip through /deadlines or
  // /my-packs/:id — those pages unmount this one, and a plain useState
  // would silently fall back to the current period on remount.
  const [searchParams, setSearchParams] = useSearchParams();
  const periodId = searchParams.get('period') || '';
  const setPeriodId = useCallback((next) => {
    setSearchParams((prev) => {
      const n = new URLSearchParams(prev);
      if (next) n.set('period', next); else n.delete('period');
      return n;
    }, { replace: true });
  }, [setSearchParams]);

  const [periods, setPeriods] = useState([]);
  const [dash, setDash] = useState(null);
  const [work, setWork] = useState(null);
  const [plans, setPlans] = useState([]);      // [{ ...PlanOut, myItems: [...] }]
  const [err, setErr] = useState('');
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    const params = periodId ? { period_id: periodId } : {};
    const [dashR, workR, packsR, perR] = await Promise.allSettled([
      teamAPI.dashboard(params),
      dashboardAPI.myPeriodWork(params),
      teamAPI.packs(params),
      periodAPI.list(),
    ]);
    setDash(dashR.status === 'fulfilled' ? dashR.value.data : null);
    setWork(workR.status === 'fulfilled' ? workR.value.data : null);
    if (perR.status === 'fulfilled') setPeriods(perR.value.data);

    if (packsR.status === 'fulfilled') {
      // Pull each pack's full tree so per-pack progress is exact
      // (period-work sub-tasks don't carry plan_id).
      const detailed = await Promise.all(
        packsR.value.data.map((p) =>
          teamAPI.packDetail(p.id)
            .then((r) => ({ ...p, tasks: r.data.tasks || [] }))
            .catch(() => ({ ...p, tasks: [] })),
        ),
      );
      setPlans(detailed);
    } else {
      setErr(getErrorMessage(packsR.reason, 'Could not load your packs.'));
    }
  }, [periodId]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  const activePeriodId = periodId || dash?.period_id || work?.period_id || null;
  const activePeriodLabel =
    periods.find((p) => p.id === activePeriodId)?.label ||
    dash?.period_label || work?.period_label || '';
  const viewingPast = !!periodId
    && periods.find((p) => p.id === periodId)?.status === 'CLOSED';

  const myItems = useMemo(() => memberLeafItems(work, user.id), [work, user.id]);

  // My work and progress per pack, from each pack's own tree. Computed by the same
  // helpers as the pack screen (/my-packs/:id), so the card and the pack always agree.
  const myStatsByPlan = useMemo(() => {
    const m = new Map();
    for (const p of plans) {
      const work = memberPackWork(p.tasks, user.id);
      m.set(p.id, { ...memberPackStats(work, user.id), hasWork: work.length > 0 });
    }
    return m;
  }, [plans, user.id]);

  const kpi = useMemo(() => {
    const c = dash?.counts || {};
    const total = dash?.task_count ?? 0;
    const completed = c.completed ?? 0;
    const review = c.pending_review ?? 0;
    const timeLogged = timeLoggedTodayStats(dash?.time_logged_today_minutes);
    return {
      overallProgress: total ? Math.round((completed / total) * 100) : 0,
      progressSub: `${completed} of ${total} of your tasks completed${activePeriodLabel ? ` · ${activePeriodLabel}` : ''}`,
      inProgress: Math.max(0, total - completed - review),
      review,
      completed,
      timeLoggedPct: timeLogged.pct,
      timeLoggedLabel: timeLogged.label,
    };
  }, [dash, activePeriodLabel]);

  // `myItems` mixes leaf work from every pack the member has tasks in, each
  // possibly a different calendar_type (now stamped per item by
  // /me/period-work) — scope the BS-label batch to just the BS items' own
  // due dates, same reasoning as bsPackDates below.
  const bsItemDates = useMemo(
    () => myItems.filter((x) => x.calendar_type === 'BS' && x.due_date).map((x) => x.due_date),
    [myItems],
  );
  const bsItemLabels = useBsDateLabels(bsItemDates, 'BS');

  const deadlines = useMemo(() => {
    return myItems
      .filter((x) => x.due_date && x.status !== 'COMPLETED')
      .sort((a, b) => a.due_date.localeCompare(b.due_date))
      .map((x) => {
        const key = memberStatusKey(x);
        return {
          tone: toneForDue(key),
          title: x.title,
          sub: x.entity_name || '',
          // The Pack's own title, straight from the backend (GET
          // /dashboards/me/period-work) — never derived from entity_name
          // or any client-side lookup, so it can't drift or go stale.
          pack: x.plan_title || '—',
          priority: PRIORITY_TO_KEY[x.priority] || 'med',
          packId: x.plan_id || null,
          taskId: x.id,
          when: dueMeta(x.due_date, key, x.calendar_type === 'BS' ? bsItemLabels : undefined),
        };
      });
  }, [myItems, bsItemLabels]);

  // This list mixes packs from potentially different calendar types, so
  // the BS-label batch is scoped to just the BS packs' own due dates — an
  // AD pack's due date must never be looked up in it, even if it happens
  // to share the same calendar date as some BS pack's due date.
  const bsPackDates = useMemo(
    () => plans.filter((p) => p.calendar_type === 'BS' && p.due_date).map((p) => p.due_date),
    [plans],
  );
  const bsLabels = useBsDateLabels(bsPackDates, 'BS');

  const packCards = useMemo(() => {
    return plans
      .filter((p) => !activePeriodId || p.period_id === activePeriodId)
      .map((p) => {
        const g = myStatsByPlan.get(p.id) || { total: 0, completed: 0, newlyAssigned: 0, pct: 0, hasWork: false };
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
          stats: {
            total: g.total, completed: g.completed, pct: g.pct, overdue: 0,
            newlyAssigned: g.newlyAssigned,
          },
          status: { key: g.total > 0 && g.completed === g.total ? 'closed' : 'open' },
          lead: personFromUser(p.owner),
          hasWork: g.hasWork, // packs holding only skipped work stay reachable
        };
      })
      .filter((c) => {
        if (c.stats.total === 0 && !c.hasWork) return false;
        if (search && !c.pack.name.toLowerCase().includes(search.toLowerCase())) return false;
        return true;
      });
  }, [plans, myStatsByPlan, activePeriodId, search, bsLabels]);

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="page-head">
          <div>
            <h1>
              Overview {activePeriodLabel && <span className="period-badge">{activePeriodLabel}</span>}
            </h1>
            <p>Open a pack to work through and complete the tasks assigned to you.</p>
          </div>
        </div>

        {err && <div className="reject-banner">{err}</div>}
        {viewingPast && (
          <div className="readonly-banner">
            Viewing <b>{activePeriodLabel}</b> — a closed period. This is a read-only
            summary of the tasks you were assigned in that period.
          </div>
        )}

        <OverviewGrid stats={kpi} variant="member" />

        <div className="dash-extra-grid solo">
          <MemberDeadlinesPanel
            items={deadlines}
            onViewAll={(bucket) => navigate(orgPath(
              `/deadlines?filter=${bucket}${periodId ? `&period=${periodId}` : ''}`,
            ))}
            onOpenTask={(d) => d.packId && navigate(orgPath(
              `/my-packs/${d.packId}?task=${d.taskId}${periodId ? `&period=${periodId}` : ''}`,
            ))}
          />
        </div>

        <div className="filter-row">
          <div className="search-box">
            <Icon name="search" size={14} />
            <input
              placeholder="Search pack"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {periods.length > 0 && (
            <label className="dd-filter period-select">
              <Icon name="calendar" size={14} />
              <select value={periodId} onChange={(e) => setPeriodId(e.target.value)}>
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

        <div className="period-grid">
          {packCards.length === 0 ? (
            <div className="empty-note">
              You have no tasks in this period yet. Your Team Lead assigns tasks from each pack.
            </div>
          ) : (
            packCards.map((c) => (
              <PackCard
                key={c.pack.id}
                pack={c.pack}
                stats={c.stats}
                status={c.status}
                lead={c.lead}
                editable={false}
                variant="member"
                onOpen={() => navigate(orgPath(
                  `/my-packs/${c.pack.id}${periodId ? `?period=${periodId}` : ''}`,
                ))}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
