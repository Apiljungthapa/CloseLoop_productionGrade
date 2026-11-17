// Team Lead dashboard — Packs overview.
//
// Replaces the old TeamLeadDashboard. Same design as the frontend_samrat
// mockup, wired to the real backend:
//   * planAPI.list           -> pack cards
//   * teamAPI.dashboard       -> KPI numbers (pending-review count)
//   * dashboardAPI.teamWorkload -> per-member load panel
//   * dashboardAPI.myLeadWorkbench -> upcoming-deadline list
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { planAPI, teamAPI, dashboardAPI, periodAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import OverviewGrid from '../components/dashboard/OverviewGrid';
import MemberDeadlinesPanel from '../components/dashboard/MemberDeadlinesPanel';
import TeamWorkload from '../components/dashboard/TeamWorkload';
import PackCard from '../components/dashboard/PackCard';
import PeriodPreviewPanel from '../components/dashboard/PeriodPreviewPanel';
import {
  packStatus, personFromUser, colorFor, fmtDate, leafItems,
  taskStatusKey, dueMeta, toneForDue, resolveCurrentPeriodId, useBsDateLabels,
  PRIORITY_TO_KEY, periodDropdownTag, timeLoggedTodayStats,
} from './leadDashUtils';

const FILTERS = ['All Packs', 'Assigned to me', 'In Progress', 'At Risk', 'Completed'];

function entityCode(name) {
  if (!name) return 'PK';
  const head = String(name).split(/[\s—–-]+/)[0];
  return head.slice(0, 3).toUpperCase();
}

export default function LeadDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const orgPath = useOrgPath();

  // Selected period lives in the URL, not just component state, so it
  // survives a refresh and a round-trip through /deadlines, /packs/:id or
  // /team/members/:userId — those pages unmount this one, and a plain
  // useState would silently fall back to the current period on remount.
  const [searchParams, setSearchParams] = useSearchParams();
  const periodId = searchParams.get('period') || ''; // '' = current (resolved below)
  const setPeriodId = useCallback((next) => {
    setSearchParams((prev) => {
      const n = new URLSearchParams(prev);
      if (next) n.set('period', next); else n.delete('period');
      return n;
    }, { replace: true });
  }, [setSearchParams]);

  const [periods, setPeriods] = useState([]);
  const [resolvedPeriodId, setResolvedPeriodId] = useState(null);
  const [plans, setPlans] = useState([]);
  const [dash, setDash] = useState(null);
  const [workload, setWorkload] = useState([]);
  const [workbench, setWorkbench] = useState(null);
  const [err, setErr] = useState('');
  // Populated only when the selected period has no real Plans yet (a
  // future period nothing has been generated for) — see the `load()`
  // effect below and PeriodPreviewPanel.
  const [preview, setPreview] = useState([]);
  const [previewLoading, setPreviewLoading] = useState(false);

  const [filter, setFilter] = useState('All Packs');
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    // Resolve which period we're scoping to *before* firing the scoped
    // calls below, instead of leaving period_id blank and inferring the
    // "current period" afterwards from whichever of dash/workbench happens
    // to come back — if both of those failed, that fallback silently
    // dropped to "no period filter" and leaked every period's packs into
    // the "Current period" view. Every call below now gets the exact same
    // explicit period_id, so scoping never depends on their success.
    let periodsList = periods;
    let effectivePeriodId = periodId;
    if (!effectivePeriodId) {
      const perFirst = await periodAPI.list().catch(() => null);
      if (perFirst) {
        periodsList = perFirst.data;
        setPeriods(periodsList);
      }
      effectivePeriodId = resolveCurrentPeriodId(periodsList);
    }

    const params = effectivePeriodId ? { period_id: effectivePeriodId } : {};
    const calls = [
      planAPI.list(params),
      teamAPI.dashboard(params),
      dashboardAPI.teamWorkload(params),
      dashboardAPI.myLeadWorkbench(params),
    ];
    // Explicit selection already has `periods` loaded (it's how the user
    // picked from the dropdown) — no need to re-fetch it sequentially.
    if (periodId) calls.push(periodAPI.list());
    const [plansR, dashR, wlR, wbR, perR] = await Promise.allSettled(calls);

    let loadedPlans = [];
    if (plansR.status === 'fulfilled') { loadedPlans = plansR.value.data; setPlans(loadedPlans); }
    else setErr(getErrorMessage(plansR.reason, 'Could not load your packs.'));
    setDash(dashR.status === 'fulfilled' ? dashR.value.data : null);
    setWorkload(wlR.status === 'fulfilled' ? (wlR.value.data.members || []) : []);
    setWorkbench(wbR.status === 'fulfilled' ? wbR.value.data : null);
    if (perR && perR.status === 'fulfilled') setPeriods(perR.value.data);
    setResolvedPeriodId(effectivePeriodId);

    // Nothing has actually been generated for this period yet (typically
    // a future one) — calculate what the Close Pack templates WOULD
    // produce instead of just showing an empty grid. The backend itself
    // returns [] once real plans exist for the period, so this is purely
    // an optimisation to skip the call in the common (current period,
    // already-generated) case.
    const scoped = loadedPlans.filter((p) => p.period_id === effectivePeriodId);
    if (effectivePeriodId && scoped.length === 0) {
      setPreviewLoading(true);
      try {
        const { data } = await planAPI.preview(effectivePeriodId);
        setPreview(data || []);
      } catch {
        setPreview([]);
      } finally {
        setPreviewLoading(false);
      }
    } else {
      setPreview([]);
    }
  }, [periodId]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  const activePeriodId = resolvedPeriodId;
  const activePeriodLabel =
    periods.find((p) => p.id === activePeriodId)?.label ||
    dash?.period_label || workbench?.period_label || '';
  const viewingPast = !!periodId
    && periods.find((p) => p.id === periodId)?.status === 'CLOSED';

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
    return plans
      .filter((p) => !activePeriodId || p.period_id === activePeriodId)
      .map((p) => {
        const overdue =
          p.due_date && new Date(p.due_date) < today && p.status !== 'CLOSED' ? 1 : 0;
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
          raw: p,
          stats,
          status: packStatus(p, stats),
          lead: personFromUser(p.owner),
          editable: p.can_edit !== false,
        };
      });
  }, [plans, activePeriodId, bsLabels]);

  const kpi = useMemo(() => {
    const total = packs.reduce((s, m) => s + m.stats.total, 0);
    const completed = packs.reduce((s, m) => s + m.stats.completed, 0);
    const timeLogged = timeLoggedTodayStats(dash?.time_logged_today_minutes);
    return {
      overallProgress: total ? Math.round((completed / total) * 100) : 0,
      progressSub: `${completed} of ${total} tasks completed across ${packs.length} pack${packs.length === 1 ? '' : 's'}${activePeriodLabel ? ` · ${activePeriodLabel}` : ''}`,
      atRisk: packs.filter((m) => m.status.key === 'risk').length,
      pendingReview: dash?.counts?.pending_review ?? 0,
      closed: packs.filter((m) => m.status.key === 'closed').length,
      timeLoggedPct: timeLogged.pct,
      timeLoggedLabel: timeLogged.label,
    };
  }, [packs, dash, activePeriodLabel]);

  // Each leaf item carries its own plan, so — same as the pack cards above
  // — an AD pack's item must never be looked up in a BS label meant for a
  // different (BS) pack that happens to share the same due date.
  const deadlinesBsDates = useMemo(() => {
    if (!workbench?.plans) return [];
    return leafItems(workbench.plans)
      .filter((x) => x.due_date && x.plan?.calendar_type === 'BS')
      .map((x) => x.due_date);
  }, [workbench]);
  const deadlinesBsLabels = useBsDateLabels(deadlinesBsDates, 'BS');

  const deadlines = useMemo(() => {
    if (!workbench?.plans) return [];
    return leafItems(workbench.plans)
      .filter((x) => x.due_date && x.status !== 'COMPLETED')
      .sort((a, b) => a.due_date.localeCompare(b.due_date))
      .map((x) => {
        const key = taskStatusKey(x);
        const who = x.assigned_to?.name || x.member_assigned_to?.name || 'Unassigned';
        const where = x.plan.entity_name || x.plan.title;
        return {
          tone: toneForDue(key),
          title: x.title,
          sub: `${who} · ${where}`,
          // The Pack's own title — never entity_name — for the deadline
          // tables' "Pack" column, same rule as the Team Member dashboard.
          pack: x.plan?.title || '—',
          priority: PRIORITY_TO_KEY[x.priority] || 'med',
          packId: x.plan?.id || null,
          // TaskDetailPanel only ever opens a top-level task — a sub-task
          // deadline deep-links to its PARENT task's id instead (the panel
          // shows sub-tasks inside it, same as clicking the task normally).
          taskId: x.kind === 'SUBTASK' ? (x.task?.id || x.id) : x.id,
          when: dueMeta(x.due_date, key, x.plan?.calendar_type === 'BS' ? deadlinesBsLabels : undefined),
        };
      });
  }, [workbench, deadlinesBsLabels]);

  // Nothing generated for the selected period — show the calculated
  // preview instead of an empty grid (see the `load()` effect above).
  const showingPreview = !!activePeriodId && packs.length === 0;

  const visible = packs.filter((m) => {
    if (filter === 'Assigned to me' && m.raw.owner?.id !== user.id) return false;
    if (filter === 'In Progress' && m.status.key !== 'open') return false;
    if (filter === 'At Risk' && m.status.key !== 'risk') return false;
    if (filter === 'Completed' && m.status.key !== 'closed') return false;
    if (search) {
      const hay = `${m.pack.name} ${m.lead?.name ?? ''}`.toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="page-head">
          <div>
            <h1>
              Packs {activePeriodLabel && <span className="period-badge">{activePeriodLabel}</span>}
            </h1>
            <p>Open a pack to manage its tasks and team.</p>
          </div>
          <div className="head-actions">
            <button className="btn" onClick={() => navigate(orgPath('/reports/teamlead'))}>
              <Icon name="download" size={15} /> Reports
            </button>
          </div>
        </div>

        {err && <div className="reject-banner">{err}</div>}
        {viewingPast && (
          <div className="readonly-banner">
            Viewing <b>{activePeriodLabel}</b> — a closed period. This is a read-only
            summary of your packs and your team&apos;s work for that period.
          </div>
        )}
        {showingPreview && !viewingPast && (
          <div className="readonly-banner">
            Viewing <b>{activePeriodLabel}</b> — nothing has been generated for this period yet.
            What&apos;s shown below is a calculated preview of the work your Close Pack templates
            and the recurring tasks you&apos;ve added would produce; it becomes real once this
            period is actually generated.
          </div>
        )}

        <OverviewGrid stats={kpi} />

        <div className="dash-extra-grid">
          <MemberDeadlinesPanel
            items={deadlines}
            onViewAll={(bucket) => navigate(orgPath(
              `/deadlines?filter=${bucket}${periodId ? `&period=${periodId}` : ''}`,
            ))}
            onOpenTask={(d) => d.packId && navigate(orgPath(
              `/packs/${d.packId}?task=${d.taskId}${periodId ? `&period=${periodId}` : ''}`,
            ))}
          />
          <TeamWorkload
            members={workload}
            onOpenMember={(m) => navigate(orgPath(
              `/team/members/${m.user_id}${periodId ? `?period=${periodId}` : ''}`,
            ))}
            onViewAll={() => navigate(orgPath(
              `/team/members${periodId ? `?period=${periodId}` : ''}`,
            ))}
          />
        </div>

        <div className="filter-row">
          {!showingPreview && (
            <div className="search-box">
              <Icon name="search" size={14} />
              <input
                placeholder="Search pack or team lead"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          )}

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

          {!showingPreview && FILTERS.map((label) => (
            <button
              key={label}
              className={`chip-filter${filter === label ? ' active' : ''}`}
              onClick={() => setFilter(label)}
            >
              {label}
            </button>
          ))}
        </div>

        {showingPreview ? (
          <PeriodPreviewPanel
            previews={preview}
            periodLabel={activePeriodLabel}
            loading={previewLoading}
          />
        ) : (
          <div className="period-grid">
            {visible.length === 0 ? (
              <div className="empty-note">No packs match this view.</div>
            ) : (
              visible.map((m) => (
                <PackCard
                  key={m.pack.id}
                  pack={m.pack}
                  stats={m.stats}
                  status={m.status}
                  lead={m.lead}
                  editable={m.editable}
                  onOpen={() => navigate(orgPath(
                    `/packs/${m.pack.id}${periodId ? `?period=${periodId}` : ''}`,
                  ))}
                />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
