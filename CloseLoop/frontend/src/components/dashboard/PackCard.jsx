import Avatar from '../ui/Avatar';

// Primary dashboard card. One per Pack (Plan).
//   variant 'lead'   (default) — pack-wide figures
//   variant 'member' — figures scoped to the signed-in Team Member's tasks
export default function PackCard({
  pack, stats, status, lead, editable = true, onOpen, variant = 'lead',
}) {
  const member = variant === 'member';
  const noneForMember = member && stats.total === 0;

  // "Count type" alert badge — the thing that made someone open this pack:
  // a Lead cares how many of ITS tasks are sitting in their review queue;
  // a Member cares how many were just handed to them and not started yet.
  // Purely a nudge on top of the existing progress bar, so it only shows
  // when there's actually something new to look at.
  const pendingReview = stats.pendingReview || 0;
  const newlyAssigned = stats.newlyAssigned || 0;

  return (
    <div className="period-card" onClick={onOpen}>
      <div className="period-top">
        <div className="period-entity">
          <div className="entity-badge" style={{ background: pack.color }}>{pack.code}</div>
          <div>
            <div className="entity-name">{pack.name}</div>
            <div className="entity-period">
              {pack.period}{pack.dueDate ? ` · Due ${pack.dueDate}` : ''}
            </div>
          </div>
        </div>
        <div className="pack-top-tags">
          {!member && pendingReview > 0 && (
            <span className="pack-alert-badge review">
              {pendingReview} pending review
            </span>
          )}
          {member && newlyAssigned > 0 && (
            <span className="pack-alert-badge assigned">
              {newlyAssigned} new
            </span>
          )}
          {!editable && !member && <span className="status-tag view-only-tag">View only</span>}
        </div>
      </div>

      <div className="period-progress-row">
        <span>Team Lead</span>
        <span className="pval">
          {lead ? (
            <span className="lead-chip">
              <Avatar
                className="lead-chip-avatar"
                initials={lead.initials}
                style={{ color: lead.color }}
              />
              <span className="lead-chip-name">{lead.name}</span>
            </span>
          ) : (
            <span className="lead-unassigned">Unassigned</span>
          )}
        </span>
      </div>

      {noneForMember ? (
        <div className="period-progress-row" style={{ marginTop: 10 }}>
          <span className="lead-unassigned">No tasks assigned to you in this pack</span>
        </div>
      ) : (
        <>
          <div className="period-progress-row" style={{ marginTop: 10 }}>
            <span>Progress</span>
            <span className="pval">
              {stats.pct}%
              {member && (
                <span className="lead-unassigned" style={{ marginLeft: 8, fontWeight: 700 }}>
                  {stats.completed}/{stats.total}
                </span>
              )}
            </span>
          </div>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{
                width: `${stats.pct}%`,
                ...(status.key === 'risk'
                  ? { background: 'linear-gradient(90deg,#E5484D,#F0817F)' }
                  : status.key === 'closed'
                    ? { background: 'linear-gradient(90deg,#1FA971,#3ECF8E)' }
                    : null),
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}
