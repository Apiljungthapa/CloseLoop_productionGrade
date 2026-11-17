// Shared "assignee" cell — an overlapping avatar stack for up to `max`
// people plus a "+N" overflow badge for the rest (hover shows their names).
// Used anywhere a task/sub-task row needs to show who it's assigned to, so
// the 3-avatars-then-overflow treatment stays consistent across the app.
//
// `highlightId` (optional) — e.g. the signed-in Team Member's own id — pulls
// that person's avatar to the front (so it's never pushed into the "+N"
// overflow) and highlights it with a ring; everyone else is shown
// de-emphasized ("in the background") instead of all avatars reading the
// same weight.
export default function AssigneeCell({ people, max = 3, highlightId }) {
  if (!people || !people.length) return <span className="lead-unassigned">Unassigned</span>;

  const ordered = highlightId
    ? [...people].sort((a, b) => (b.id === highlightId) - (a.id === highlightId))
    : people;
  const shown = ordered.slice(0, max);
  const hidden = ordered.slice(max);

  return (
    <div className="assignee-cell-multi">
      <div className="avatar-stack">
        {shown.map((p, i) => {
          const isMe = !!highlightId && p.id === highlightId;
          return (
            <div
              className="m-avatar"
              key={p.id ?? i}
              style={{
                background: p.color,
                opacity: highlightId && !isMe ? 0.55 : 1,
                boxShadow: isMe ? '0 0 0 2px var(--blue)' : undefined,
                // The stack overlaps later avatars on top of earlier ones
                // (negative margin-left, natural paint order) — without an
                // explicit stacking context "you" (always first) would sit
                // BEHIND the others despite being highlighted. Lift it above.
                position: 'relative',
                zIndex: isMe ? 1 : 0,
              }}
              title={isMe ? `${p.name} (You)` : p.name}
            >
              {p.initials}
            </div>
          );
        })}
        {hidden.length > 0 && (
          <div
            className="m-avatar avatar-more"
            style={{ background: '#8A90A6' }}
            title={hidden.map((p) => p.name).join(', ')}
          >
            +{hidden.length}
          </div>
        )}
      </div>
      <div className="assignee-name">
        {people.length === 1 ? people[0].name : `${people.length} assignees`}
      </div>
    </div>
  );
}
