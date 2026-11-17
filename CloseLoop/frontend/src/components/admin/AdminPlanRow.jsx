import { useNavigate } from 'react-router-dom';
import { useOrgPath } from '../../orgPath.js';
import Avatar from '../ui/Avatar';

// One row in the Admin "All Plans" list — a pack summarised for
// tenant-wide management. Clicking it (row or status pill) opens the
// Super-Admin pack detail page.
export default function AdminPlanRow({ pack, stats, status, lead }) {
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const open = () => navigate(orgPath(`/admin-packs/${pack.id}`));

  return (
    <div
      className="admin-plan-row"
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
        }
      }}
    >
      <div>
        <div className="admin-plan-name">{pack.name}</div>
        <div className="admin-plan-meta">
          {pack.period} · {pack.code} · Due {pack.dueDate || '—'} · {stats.total} task{stats.total === 1 ? '' : 's'} ·{' '}
          {lead ? (
            <span className="lead-inline">
              <Avatar
                className="admin-lead-avatar"
                initials={lead.initials}
                color={lead.color}
              />
              {lead.name}
            </span>
          ) : (
            'Unassigned'
          )}
        </div>
      </div>

      <div className="admin-plan-progress">
        <div className="app-track">
          <div className="app-label">
            <span>Progress</span>
            <span>{stats.pct}%</span>
          </div>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${stats.pct}%` }} />
          </div>
        </div>

        <button
          type="button"
          className={`admin-status-btn ${status.tag}`}
          onClick={(e) => {
            e.stopPropagation();
            open();
          }}
        >
          {status.label}
        </button>
      </div>
    </div>
  );
}
