// Full "Upcoming Deadlines" list — the page behind the dashboard panel's
// "View all" link. Role-aware: a Team Lead sees every open task across the
// packs they manage; a Team Member sees their own open work items.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { dashboardAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import {
  leafItems, memberLeafItems, taskStatusKey, memberStatusKey,
  dueMeta, toneForDue, fmtDate, useBsDateLabels,
} from './leadDashUtils';

export default function DeadlinesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const isLead = user.role === 'TEAM_LEAD';
  const [searchParams] = useSearchParams();
  // From MemberDeadlinesPanel's per-table "View all" row: 'today' | 'missed'.
  const toneFilter = { today: 'amber', missed: 'red' }[searchParams.get('filter')] || null;
  // Carried from LeadDashboard/MemberDashboard's own URL so this page (and
  // the back-link / row links below) stay scoped to whatever period was
  // being viewed, instead of always falling back to the current period.
  const periodParam = searchParams.get('period') || '';

  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const params = periodParam ? { period_id: periodParam } : {};
      const r = isLead
        ? await dashboardAPI.myLeadWorkbench(params)
        : await dashboardAPI.myPeriodWork(params);
      setData(r.data);
      setErr('');
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not load deadlines.'));
    }
  }, [isLead, periodParam]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  // Each leaf item carries its own plan, so the BS-label batch is scoped
  // to just the BS packs' own due dates — an AD pack's item must never be
  // looked up in a BS label meant for a different (BS) pack that happens
  // to share the same due date.
  const bsDates = useMemo(() => {
    if (!data || !isLead) return [];
    return leafItems(data.plans || [])
      .filter((x) => x.due_date && x.plan?.calendar_type === 'BS')
      .map((x) => x.due_date);
  }, [data, isLead]);
  const bsLabels = useBsDateLabels(bsDates, 'BS');

  const rows = useMemo(() => {
    if (!data) return [];
    if (isLead) {
      return leafItems(data.plans || [])
        .filter((x) => x.due_date && x.status !== 'COMPLETED')
        .sort((a, b) => a.due_date.localeCompare(b.due_date))
        .map((x) => {
          const key = taskStatusKey(x);
          const who = x.assigned_to?.name || x.member_assigned_to?.name || 'Unassigned';
          const itemBsLabels = x.plan?.calendar_type === 'BS' ? bsLabels : undefined;
          return {
            id: x.id,
            planId: x.plan?.id || null,
            title: x.title,
            sub: `${who} · ${x.plan?.entity_name || x.plan?.title || ''}`,
            due: fmtDate(x.due_date, itemBsLabels),
            when: dueMeta(x.due_date, key, itemBsLabels),
            tone: toneForDue(key),
          };
        });
    }
    return memberLeafItems(data, user.id)
      .filter((x) => x.due_date && x.status !== 'COMPLETED')
      .sort((a, b) => a.due_date.localeCompare(b.due_date))
      .map((x) => {
        const key = memberStatusKey(x);
        return {
          id: x.id,
          planId: x.plan_id || null,
          title: x.title,
          // Same source of truth as the dashboard's deadline tables —
          // the Pack's own title from the backend, never entity_name.
          sub: x.plan_title || '—',
          due: fmtDate(x.due_date),
          when: dueMeta(x.due_date, key),
          tone: toneForDue(key),
        };
      });
  }, [data, isLead, bsLabels, user.id]);

  const filteredRows = useMemo(
    () => (toneFilter ? rows.filter((r) => r.tone === toneFilter) : rows),
    [rows, toneFilter],
  );
  const heading = toneFilter === 'amber' ? "Today's Tasks" : toneFilter === 'red' ? 'Missed Deadlines' : 'Upcoming Deadlines';

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="back-link" onClick={() => navigate(orgPath(
          `/${periodParam ? `?period=${periodParam}` : ''}`,
        ))}>
          <Icon name="chevron-left" size={13} strokeWidth={2.4} /> Overview
        </div>

        <div className="page-head">
          <div>
            <h1>
              {heading}
              {filteredRows.length > 0 && <span className="section-count" style={{ marginLeft: 10 }}>{filteredRows.length}</span>}
            </h1>
            <p>Every open task due, soonest first.</p>
          </div>
        </div>

        {err && <div className="reject-banner">{err}</div>}

        <div className="panel-card">
          {!data ? (
            <div className="empty-note">Loading…</div>
          ) : filteredRows.length === 0 ? (
            <div className="empty-note">Nothing due — you&apos;re all caught up.</div>
          ) : (
            filteredRows.map((d) => (
              <div
                key={d.id}
                className={`deadline-item${d.planId ? ' deadline-item-click' : ''}`}
                onClick={d.planId
                  ? () => navigate(orgPath(
                    `${isLead ? '/packs/' : '/my-packs/'}${d.planId}${periodParam ? `?period=${periodParam}` : ''}`,
                  ))
                  : undefined}
              >
                <div className={`dl-dot ${d.tone}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="dl-title">{d.title}</div>
                  <div className="dl-sub">{d.sub}</div>
                </div>
                <div className="dl-when-col">
                  <div className={`dl-when ${d.tone}`}>{d.when}</div>
                  {!d.when.startsWith('Due ') && <div className="dl-date">{d.due}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
