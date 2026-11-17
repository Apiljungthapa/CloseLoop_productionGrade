// Horizontal status tabs with counts. Controlled by the parent page.
export default function StatusTabs({ tabs, active, onChange }) {
  return (
    <div className="tabs-row">
      {tabs.map((t) => (
        <div
          key={t.label}
          className={`tab${active === t.status ? ' active' : ''}`}
          onClick={() => onChange(t.status)}
        >
          {t.label} <span className="count">{t.count}</span>
        </div>
      ))}
    </div>
  );
}
