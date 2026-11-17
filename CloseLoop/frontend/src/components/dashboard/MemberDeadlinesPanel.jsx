// Team Member dashboard only — same deadline data/logic as before (items:
// [{ tone, title, pack, priority, when, packId, taskId }]), split into two
// always-visible mini-tables: Today and Missed (tone 'amber'/'red'). No
// Upcoming table. Clicking a row opens that task inside its Pack; a "View
// all" row at the bottom of each table goes to the full list for that
// bucket (not a panel-level link).
const VISIBLE = 4;

const PRIORITY_LABEL = { high: 'High', med: 'Medium', low: 'Low' };

function Column({ heading, bucket, items, emptyText, onOpenTask, onViewAll }) {
  return (
    <div className="mdp-col">
      <div className="mdp-heading">
        {heading} <span className="mdp-count">{items.length}</span>
      </div>
      <div className="mdp-thead">
        <span>Task</span><span>Pack</span><span>Due</span><span>Priority</span>
      </div>
      <div className="mdp-rows">
        {items.length === 0 ? (
          <div className="empty-note">{emptyText}</div>
        ) : (
          items.slice(0, VISIBLE).map((d, i) => (
            <div
              className="mdp-row mdp-row-click"
              key={d.taskId ?? i}
              role="button"
              tabIndex={0}
              onClick={() => onOpenTask?.(d)}
              onKeyDown={(e) => { if (e.key === 'Enter') onOpenTask?.(d); }}
            >
              <span className="mdp-title">{d.title}</span>
              <span className="mdp-sub">{d.pack || '—'}</span>
              <span className={`mdp-when ${d.tone}`}>
                {d.when}<span className={`dl-dot ${d.tone}`} />
              </span>
              <span className={`mdp-priority p-${d.priority}`}>{PRIORITY_LABEL[d.priority] || 'Medium'}</span>
            </div>
          ))
        )}
      </div>
      {onViewAll && (
        <div className="mdp-viewall" onClick={() => onViewAll(bucket)}>
          View all {heading.toLowerCase()} ({items.length})
        </div>
      )}
    </div>
  );
}

// props: items (tone/title/pack/priority/when/packId/taskId), onViewAll(bucket), onOpenTask
export default function MemberDeadlinesPanel({ items = [], onViewAll, onOpenTask }) {
  const today = items.filter((d) => d.tone === 'amber');
  const missed = items.filter((d) => d.tone === 'red');

  return (
    <div className="panel-card mdp-panel">
      <div className="panel-head">
        <h3>My Deadlines</h3>
      </div>
      <div className="mdp-grid mdp-grid-2">
        <Column
          heading="Today" bucket="today" items={today}
          emptyText="Nothing due today — you're all caught up."
          onOpenTask={onOpenTask} onViewAll={onViewAll}
        />
        <Column
          heading="Missed" bucket="missed" items={missed}
          emptyText="No missed deadlines — nice work."
          onOpenTask={onOpenTask} onViewAll={onViewAll}
        />
      </div>
    </div>
  );
}
