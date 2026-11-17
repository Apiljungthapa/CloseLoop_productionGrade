import { useNavigate } from 'react-router-dom';
import { useOrgPath } from '../../orgPath.js';
import Icon from '../ui/Icon';

// Right-column "Team" card on the Super Admin Dashboard — headcount plus a
// shortcut into the Users section. `users` is the tenant's full user list
// (already fetched by the parent dashboard page).
export default function AdminTeamPanel({ users = [] }) {
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const leads = users.filter((u) => u.role === 'TEAM_LEAD').length;
  const members = users.filter((u) => u.role === 'TEAM_MEMBER').length;

  return (
    <div className="panel-card admin-team-panel">
      <div className="panel-head">
        <h3 className="panel-head-icon-title">
          <Icon name="users" size={15} /> Team
        </h3>
      </div>

      <div className="atp-row">
        <span>Team Leads</span>
        <b>{leads}</b>
      </div>
      <div className="atp-row">
        <span>Team Members</span>
        <b>{members}</b>
      </div>
      <div className="atp-row">
        <span>Total users</span>
        <b>{users.length}</b>
      </div>

      <button type="button" className="btn admin-manage-btn" onClick={() => navigate(orgPath('/users'))}>
        Manage users
      </button>
    </div>
  );
}
