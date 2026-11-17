// Upcoming / overdue leaf work items.
// `items` — the FULL list [{ tone, title, sub, when }] built by the parent
// page. This panel shows at most VISIBLE of them; "View all" (a link to
// the dedicated deadlines page) only appears when there are more than that.

const VISIBLE = 5;

export default function UpcomingDeadlines({
  items = [],
  onViewAll,
  title = 'Upcoming Deadlines',
  emptyText = "Nothing due soon — you're all caught up.",
}) {
  const shown = items.slice(0, VISIBLE);
  const hasMore = onViewAll && items.length > VISIBLE;

  return (
    <div className="panel-card">
      <div className="panel-head">
        <h3>{title}</h3>
        {hasMore && (
          <span className="view-all" onClick={onViewAll}>View all ({items.length})</span>
        )}
      </div>
      {items.length === 0 ? (
        <div className="empty-note">{emptyText}</div>
      ) : (
        shown.map((d, i) => (
          <div className="deadline-item" key={i}>
            <div className={`dl-dot ${d.tone}`} />
            <div>
              <div className="dl-title">{d.title}</div>
              <div className="dl-sub">{d.sub}</div>
            </div>
            <div className={`dl-when ${d.tone}`}>{d.when}</div>
          </div>
        ))
      )}
    </div>
  );
}
