import React from 'react';
import { Link } from 'react-router-dom';
import { useOrgPath } from '../../../orgPath.js';

// Sits above the classic reports: the way in to Report Studio (plain-language reports with KPIs, charts,
// findings and PDF / Excel / CSV downloads).
export default function StudioBanner() {
  const orgPath = useOrgPath();
  return (
    <Link
      to={orgPath('/reports/studio')}
      className="d-flex align-items-center gap-3 p-3 mb-3 rounded-3 text-decoration-none"
      style={{ background: 'linear-gradient(90deg, #eef2ff 0%, #f6f8fb 100%)', border: '1px solid #d9e0f5', color: '#1f2433' }}
    >
      <i className="bi bi-stars" style={{ fontSize: '1.6rem', color: '#2f55d4' }} aria-hidden="true" />
      <span className="flex-grow-1">
        <b>New: Report Studio</b>
        <span className="d-block small text-muted">
          Ask for a report in plain language, e.g. “Compare this period with the previous period”, and get KPIs, charts, key
          findings and recommended actions as a dashboard, PDF, Excel or CSV.
        </span>
      </span>
      <i className="bi bi-arrow-right" aria-hidden="true" />
    </Link>
  );
}
