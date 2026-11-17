// Team Lead Reports — subset of reports appropriate for Team Lead role
import React from 'react';
import ReportView from '../components/ReportView.jsx';
import StudioBanner from '../components/reports/studio/StudioBanner.jsx';

export default function TeamLeadReports() {
  return (
    <>
    <StudioBanner />
    <ReportView
      title="Team Reports"
      subtitle="Track completion, performance, and workload activity across your scope."
      availableReports={[
        'current-period-status',
        'period-close-completion',
        'overdue-tasks',
        'user-workload',
        'time-entries'
      ]}
      defaultReport="current-period-status"
    />
    </>
  );
}
