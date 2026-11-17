import Icon from '../ui/Icon';

// One of the four summary tiles at the top of the Super Admin Dashboard.
export default function AdminStatCard({ tone, icon, label, value }) {
  return (
    <div className={`card admin-stat-card ${tone}`}>
      <div className="asc-icon">
        <Icon name={icon} size={19} />
      </div>
      <div>
        <div className="asc-lbl">{label}</div>
        <div className="asc-num">{value}</div>
      </div>
    </div>
  );
}
