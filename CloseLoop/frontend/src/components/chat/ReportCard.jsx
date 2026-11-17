import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useOrgPath } from '../../orgPath.js';
import { downloadReport, FORMAT_LABEL } from '../reports/studio/download.js';

// The card Close Bhai shows when a report was asked for in chat: headline figures, the first findings, and
// buttons to open the full dashboard or download the file. The report itself is built (and permission-checked)
// by the backend; the card only carries the request ("spec") back to it.

const ICON = { pdf: 'bi-file-earmark-pdf', xlsx: 'bi-file-earmark-excel', csv: 'bi-filetype-csv' };

export default function ReportCard({ report, tabIndex = 0, onNavigate }) {
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  const openDashboard = () => {
    onNavigate?.();
    navigate(`${orgPath('/reports/studio')}?run=${encodeURIComponent(JSON.stringify(report.spec))}`);
  };
  const download = async (format) => {
    setBusy(format);
    setError('');
    setDone('');
    try {
      const name = await downloadReport(report.spec, format);
      setDone(`Saved ${name}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const wanted = report.formats?.[0];
  return (
    <div className="clchat-report" role="group" aria-label={report.title}>
      <div className="clchat-report-title">
        <i className="bi bi-bar-chart-line" aria-hidden="true" />
        <div>
          <b>{report.title}</b>
          <small>{report.subtitle}</small>
        </div>
      </div>
      <div className="clchat-report-kpis">
        {report.kpis.map((k) => (
          <div key={k.key} className={`clchat-report-kpi clchat-tone-${k.tone}`}>
            <small>{k.label}</small>
            <b>{k.display}</b>
            {k.delta_display && <span className={`clchat-delta clchat-text-${k.delta_tone}`}>{k.delta_display}</span>}
          </div>
        ))}
      </div>
      <div className="clchat-report-actions">
        <button type="button" className="clchat-report-open" onClick={openDashboard} tabIndex={tabIndex}>
          <i className="bi bi-grid-1x2" aria-hidden="true" /> Open dashboard
        </button>
        {(report.downloads || []).map((f) => (
          <button
            key={f} type="button" tabIndex={tabIndex} disabled={!!busy}
            className={`clchat-report-dl${wanted === f ? ' clchat-report-dl-wanted' : ''}`} onClick={() => download(f)}
          >
            <i className={`bi ${busy === f ? 'bi-hourglass-split' : ICON[f]}`} aria-hidden="true" /> {FORMAT_LABEL[f]}
          </button>
        ))}
      </div>
      {error && <small className="clchat-report-error" role="alert">{error}</small>}
      {done && <small className="clchat-report-done">{done}</small>}
    </div>
  );
}
