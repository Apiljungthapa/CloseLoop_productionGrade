import { Link } from 'react-router-dom';
import { ProgressBar } from '../ui/ProgressBar';
import Icon from '../ui/Icon';
import { useOrgPath } from '../../orgPath.js';

// Dashboard summary cards.
//   variant 'lead'   (default) — pack-level metrics across the whole close
//   variant 'member' — the same cards scoped to the signed-in Team Member
export default function OverviewGrid({ stats, variant = 'lead' }) {
  const member = variant === 'member';
  const orgPath = useOrgPath();
  const s = stats || {
    overallProgress: 0, progressSub: '',
    atRisk: 0, pendingReview: 0, closed: 0,
    inProgress: 0, review: 0, completed: 0,
    timeLoggedPct: 0, timeLoggedLabel: '0h 00m / 8h',
  };
  return (
    <div className="overview-grid">
      <div className="card team-progress-card">
        <div className="card-top">
          <h3>{member ? 'My Progress' : 'Overall Progress — All Packs'}</h3>
          <span className="pct">{s.overallProgress}%</span>
        </div>
        <ProgressBar pct={s.overallProgress} />
        <div className="card-sub">{s.progressSub}</div>
      </div>

      <div className="card team-progress-card time-logged-card">
        <div className="card-top">
          <h3>Time Logged Today</h3>
          <span className="pct">{s.timeLoggedPct}%</span>
        </div>
        <ProgressBar pct={s.timeLoggedPct} />
        <div className="time-logged-foot">
          <div className="card-sub">{s.timeLoggedLabel}</div>
          <Link to={orgPath('/time-log')} className="time-log-link">
            View history <Icon name="chevron-right" size={12} strokeWidth={2.6} />
          </Link>
        </div>
      </div>

      {member ? (
        <>
          <div className="card stat-card">
            <div className="num">{s.inProgress}</div>
            <div className="lbl">My tasks in progress</div>
          </div>
          <div className="card stat-card review">
            <div className="num">{s.review}</div>
            <div className="lbl">Submitted for review</div>
          </div>
          <div className="card stat-card done">
            <div className="num">{s.completed}</div>
            <div className="lbl">Completed</div>
          </div>
        </>
      ) : (
        <>
          <div className="card stat-card overdue">
            <div className="num">{s.atRisk}</div>
            <div className="lbl">Packs at risk</div>
          </div>
          <div className="card stat-card review">
            <div className="num">{s.pendingReview}</div>
            <div className="lbl">Pending your review</div>
          </div>
          <div className="card stat-card done">
            <div className="num">{s.closed}</div>
            <div className="lbl">Packs completed</div>
          </div>
        </>
      )}
    </div>
  );
}
