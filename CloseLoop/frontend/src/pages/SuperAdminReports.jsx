// SuperAdmin Reports — all 5 reports available
import React from 'react';
import ReportView from '../components/ReportView.jsx';
import StudioBanner from '../components/reports/studio/StudioBanner.jsx';

export default function SuperAdminReports() {
  return (
    <>
    <StudioBanner />
    <ReportView
      title="Reports"
      subtitle="Comprehensive analytics across all entities, periods, groups, and teams."
      availableReports={[
        'current-period-status',
        'period-close-completion',
        'overdue-tasks',
        'user-workload',
        'year-end-rollup',
        'time-entries',
      ]}
      defaultReport="current-period-status"
    />
    </>
  );
}
