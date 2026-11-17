import { statusMeta } from '../../data/meta';

// Status pill (Not Started / In Progress / Overdue / Completed / Pending Review ...).
// `lateDays` > 0 tags a completed-but-late item with how many days it overran
// its due date, so "Completed" never hides that the work landed late.
export default function Badge({ status, id, lateDays = 0 }) {
  const meta = statusMeta[status] ?? statusMeta.notstarted;
  return (
    <span className="badge-wrap" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <span className={`badge ${meta.cls}`} id={id}>
        <span className="bdot" />
        {meta.label}
      </span>
      {lateDays > 0 && (
        <span
          className="badge-late"
          title={`Completed ${lateDays} day${lateDays === 1 ? '' : 's'} after the due date`}
          style={{
            color: 'var(--red, #E5484D)',
            fontSize: 11,
            fontWeight: 700,
            whiteSpace: 'nowrap',
          }}
        >
          {lateDays}d late
        </span>
      )}
    </span>
  );
}
