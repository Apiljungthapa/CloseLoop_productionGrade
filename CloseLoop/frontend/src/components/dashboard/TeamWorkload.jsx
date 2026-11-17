import { initialsOf, colorFor } from '../../pages/leadDashUtils';

// Per-person open-work load for the period (from GET /api/dashboards/team-workload).
// `members` — TeamWorkloadMemberOut[]: { user_id, name, load_pct, overloaded }
// `onOpenMember(member)` — optional: makes each row open that member's task list.
// `onViewAll()` — optional: shows the "View All Members" link, which routes
// to the dedicated all-members page (TeamMembersPage).
//
// Box height is fixed to match the My Deadlines panel beside it (both grid
// items stretch to the row's tallest — see .dash-extra-grid in leaddash.css)
// and only ever shows 3 members at once; the rest scroll inside .tw-list.
export default function TeamWorkload({ members = [], onOpenMember, onViewAll }) {
  return (
    <div className="panel-card tw-panel">
      <div className="panel-head">
        <h3>Team Workload</h3>
        {members.length > 0 && (
          <span className="tw-count">{members.length} member{members.length === 1 ? '' : 's'}</span>
        )}
      </div>
      <div className="panel-sub">Open task load per person this period</div>

      {members.length === 0 ? (
        <div className="empty-note">No work assigned to your team yet this period.</div>
      ) : (
        <div className="tw-list">
          {members.map((m) => {
            const pct = Math.round(m.load_pct ?? 0);
            const tone = pct >= 80 ? 'var(--red)' : pct >= 55 ? 'var(--amber)' : 'var(--green)';
            const clickable = !!onOpenMember;
            return (
              <div
                className={`workload-row${clickable ? ' workload-row-click' : ''}`}
                key={m.user_id}
                role={clickable ? 'button' : undefined}
                tabIndex={clickable ? 0 : undefined}
                onClick={clickable ? () => onOpenMember(m) : undefined}
                onKeyDown={clickable ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenMember(m); }
                } : undefined}
                title={clickable ? `View ${m.name}'s tasks` : undefined}
              >
                <div className="workload-top">
                  <div className="wl-avatar" style={{ background: colorFor(m.user_id) }}>
                    {initialsOf(m.name)}
                  </div>
                  <div className="wl-name">{m.name}</div>
                  <div className="wl-pct" style={{ color: tone }}>{pct}%</div>
                </div>
                <div className="workload-bar-track">
                  <div className="workload-bar-fill" style={{ width: `${pct}%`, background: tone }} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {onViewAll && (
        <div className="mdp-viewall" onClick={onViewAll}>View All Members</div>
      )}
    </div>
  );
}
