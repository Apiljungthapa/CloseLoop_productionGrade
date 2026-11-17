// Progress visualisations used around the app.

// Full-width track (dashboard cards, detail panel).
export function ProgressBar({ pct, fillStyle }) {
  return (
    <div className="bar-track">
      <div className="bar-fill" style={{ width: `${pct}%`, ...(fillStyle ? { background: fillStyle } : null) }} />
    </div>
  );
}

// Compact "mini" bar + fraction used in the task tables.
export function MiniProgress({ value = [0, 1], blue = true }) {
  const [done, total] = value;
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="progress-cell">
      <div className="mini-bar-track">
        <div className={`mini-bar-fill${blue ? ' blue' : ''}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="prog-frac">{done}/{total}</span>
    </div>
  );
}
