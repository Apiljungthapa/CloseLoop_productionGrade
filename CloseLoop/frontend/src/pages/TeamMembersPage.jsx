// Full team roster + workload — the page behind the Team Workload panel's
// "View All Members" link. Same data as that panel (GET
// /dashboards/team-workload), just unbounded and sortable-by-eye instead of
// capped to 3 visible rows. Clicking a member opens MemberTasksView (their
// assigned tasks for the same period), same as clicking a row in the panel.
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useOrgPath } from '../orgPath.js';
import { useLiveRefresh } from '../components/useLiveRefresh.js';
import { dashboardAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import { initialsOf, colorFor } from './leadDashUtils';

const ROLE_PILL = {
  TEAM_LEAD: 'role-pill-lead',
  TEAM_MEMBER: 'role-pill-member',
};

export default function TeamMembersPage() {
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const [searchParams] = useSearchParams();
  const periodParam = searchParams.get('period') || '';

  const [members, setMembers] = useState([]);
  const [periodLabel, setPeriodLabel] = useState('');
  const [err, setErr] = useState('');
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = periodParam ? { period_id: periodParam } : {};
      const { data } = await dashboardAPI.teamWorkload(params);
      setMembers(data.members || []);
      setPeriodLabel(data.period_label || '');
      setErr('');
    } catch (e) {
      setErr(getErrorMessage(e, 'Could not load team workload.'));
    } finally {
      setLoaded(true);
    }
  }, [periodParam]);

  useEffect(() => { load(); }, [load]);
  useLiveRefresh(load);

  const overloadedCount = members.filter((m) => m.overloaded).length;

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
            <div className="admin-title-row">
              <Icon name="users" size={22} style={{ color: 'var(--blue)' }} />
              <h1>Team Members</h1>
            </div>
            <p>
              {members.length} member{members.length === 1 ? '' : 's'}
              {periodLabel ? ` · ${periodLabel}` : ''}
              {overloadedCount > 0 && ` · ${overloadedCount} over 80% load`}
            </p>
          </div>
        </div>

        {err && <div className="reject-banner">{err}</div>}

        <div className="users-table-card">
          <table>
            <thead>
              <tr>
                <th>Member</th>
                <th>Role</th>
                <th>Assigned</th>
                <th>Open</th>
                <th>Load</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {!loaded ? (
                <tr><td colSpan={6} className="users-empty">Loading…</td></tr>
              ) : members.length === 0 ? (
                <tr><td colSpan={6} className="users-empty">No work assigned to your team yet this period.</td></tr>
              ) : members.map((m) => {
                const pct = Math.round(m.load_pct ?? 0);
                const tone = pct >= 80 ? 'var(--red)' : pct >= 55 ? 'var(--amber)' : 'var(--green)';
                const goToMember = () => navigate(orgPath(
                  `/team/members/${m.user_id}${periodParam ? `?period=${periodParam}` : ''}`,
                ));
                return (
                  <tr
                    key={m.user_id}
                    className="row-click"
                    onClick={goToMember}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goToMember(); } }}
                  >
                    <td>
                      <div className="user-cell">
                        <div className="u-avatar" style={{ background: colorFor(m.user_id) }}>
                          {initialsOf(m.name)}
                        </div>
                        <div className="u-id">
                          <div className="u-name" style={{ color: 'var(--blue)' }}>{m.name}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className={`role-pill ${ROLE_PILL[m.role] || ''}`}>
                        {String(m.role).replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="u-muted">{m.total_count}</td>
                    <td className="u-muted">{m.open_count}</td>
                    <td style={{ minWidth: 140 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div className="workload-bar-track" style={{ flex: 1, marginLeft: 0 }}>
                          <div className="workload-bar-fill" style={{ width: `${pct}%`, background: tone }} />
                        </div>
                        <span style={{ color: tone, fontWeight: 800, fontSize: 12.5 }}>{pct}%</span>
                      </div>
                    </td>
                    <td>
                      <span style={{ color: m.overloaded ? 'var(--red)' : 'var(--green)', fontWeight: 700, fontSize: 12.5 }}>
                        {m.overloaded ? 'Overloaded' : 'Balanced'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
