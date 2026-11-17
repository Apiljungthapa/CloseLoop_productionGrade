import { priorityMeta } from '../../data/meta';

// Coloured "High / Medium / Low Priority" label with a leading dot.
export default function PriorityTag({ priority }) {
  const meta = priorityMeta[priority] ?? priorityMeta.low;
  return (
    <span className={`priority ${meta.cls}`}>
      <span className="dot" />
      {meta.label}
    </span>
  );
}
