import React, { useState } from 'react';
import { Button, Spinner } from 'react-bootstrap';
import { StudioChart } from './charts.jsx';
import { FORMAT_LABEL } from './download.js';

// Renders one RichReport (see backend services/report_studio.py): header, KPI cards, findings, recommended
// actions, charts, tables and notes. Pure presentation: the caller supplies the report and the download handler.

const SEVERITY_ICON = { good: 'bi-check-circle-fill', info: 'bi-info-circle-fill', warn: 'bi-exclamation-triangle-fill', bad: 'bi-x-octagon-fill' };
const READINESS_TONE = { Closed: 'good', 'Ready to close': 'good', 'On track': 'good', 'At risk': 'warn', Behind: 'bad', 'No tasks': 'muted' };
const WORSE_WHEN_HIGHER = new Set(['Open', 'Overdue', 'Waiting for review', 'Unassigned open']);
const PREVIEW_ROWS = 25;

function fmtDate(value) {
  if (!value) return '–';
  const d = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function formatCell(col, value) {
  if (value === null || value === undefined || value === '') return col.type === 'text' ? '' : '–';
  switch (col.type) {
    case 'percent': return `${Number(value).toFixed(1)}%`;
    case 'int':
    case 'days': return Number(value).toLocaleString();
    case 'number': return typeof value === 'number' ? (Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1)) : String(value);
    case 'date': return fmtDate(value);
    default: return String(value);
  }
}

// In a comparison table a rise is good for some figures and bad for others.
function changeTone(table, row, col) {
  const v = row[col.key];
  if (typeof v !== 'number' || v === 0) return '';
  const lowerIsBetter = table.id === 'kpi_compare' ? WORSE_WHEN_HIGHER.has(row.metric) : col.key === 'delta_overdue';
  return (v > 0) === lowerIsBetter ? 'bad' : 'good';
}
const isChangeCol = (col) => ['change', 'delta', 'delta_overdue'].includes(col.key);

function Kpi({ k }) {
  return (
    <div className={`rs-kpi rs-tone-${k.tone}`}>
      <span className="rs-kpi-label">{k.label}</span>
      <span className="rs-kpi-value">{k.display}</span>
      {k.delta_display && (
        <span className={`rs-kpi-delta rs-text-${k.delta_tone}`}>
          <i className={`bi ${k.delta > 0 ? 'bi-arrow-up-right' : k.delta < 0 ? 'bi-arrow-down-right' : 'bi-dash'}`} aria-hidden="true" /> {k.delta_display}
        </span>
      )}
      {k.hint && <span className="rs-kpi-hint">{k.hint}</span>}
    </div>
  );
}

function DataTable({ table }) {
  const [all, setAll] = useState(false);
  const rows = all ? table.rows : table.rows.slice(0, PREVIEW_ROWS);
  return (
    <section className="rs-table-card" data-table={table.id}>
      <h3>{table.title}</h3>
      {table.rows.length === 0 ? (
        <div className="rs-empty">Nothing to list.</div>
      ) : (
        <div className="rs-table-scroll">
          <table className="rs-table">
            <thead>
              <tr>{table.columns.map((c) => <th key={c.key} className={c.type !== 'text' ? 'rs-num' : ''}>{c.label}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {table.columns.map((c) => {
                    const text = formatCell(c, row[c.key]);
                    if (c.type === 'badge') {
                      return <td key={c.key}><span className={`rs-badge rs-tone-${READINESS_TONE[text] || 'muted'}`}>{text}</span></td>;
                    }
                    const tone = isChangeCol(c) ? changeTone(table, row, c) : '';
                    const signed = isChangeCol(c) && typeof row[c.key] === 'number' && row[c.key] > 0 ? `+${text}` : text;
                    return <td key={c.key} className={`${c.type !== 'text' ? 'rs-num' : ''}${tone ? ` rs-text-${tone}` : ''}`}>{signed}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {table.rows.length > PREVIEW_ROWS && (
        <button type="button" className="rs-link" onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer rows' : `Show all ${table.total_rows || table.rows.length} rows`}
        </button>
      )}
      {(table.note || table.total_rows) && (
        <small className="rs-note">{table.note}{table.total_rows ? ` The first ${table.rows.length} of ${table.total_rows} rows are shown; downloads contain the same rows.` : ''}</small>
      )}
    </section>
  );
}

export default function StudioReport({ report, onDownload, downloading }) {
  const scope = report.scope || {};
  return (
    <article className="rs-report" aria-label={report.title}>
      <header className="rs-report-head">
        <div>
          <h2>{report.title}</h2>
          <p>{report.subtitle}</p>
          <div className="rs-chips">
            {scope.period && <span className="rs-chip"><i className="bi bi-calendar3" aria-hidden="true" /> {scope.period.label}</span>}
            {scope.compare_period && <span className="rs-chip"><i className="bi bi-arrow-left-right" aria-hidden="true" /> vs {scope.compare_period.label}</span>}
            {scope.entity && <span className="rs-chip"><i className="bi bi-diagram-3" aria-hidden="true" /> {scope.entity}</span>}
            {scope.member && <span className="rs-chip"><i className="bi bi-person" aria-hidden="true" /> {scope.member}</span>}
          </div>
        </div>
        <div className="rs-downloads" role="group" aria-label="Download this report">
          {['pdf', 'xlsx', 'csv'].map((f) => (
            <Button key={f} size="sm" variant="outline-primary" disabled={!!downloading} onClick={() => onDownload(f)}>
              {downloading === f ? <Spinner size="sm" animation="border" /> : <i className={`bi ${f === 'pdf' ? 'bi-file-earmark-pdf' : f === 'xlsx' ? 'bi-file-earmark-excel' : 'bi-filetype-csv'}`} aria-hidden="true" />}{' '}
              {FORMAT_LABEL[f]}
            </Button>
          ))}
          <Button size="sm" variant="outline-secondary" onClick={() => window.print()}>
            <i className="bi bi-printer" aria-hidden="true" /> Print
          </Button>
        </div>
      </header>

      <div className="rs-kpis">{report.kpis.map((k) => <Kpi key={k.key} k={k} />)}</div>

      <div className="rs-insights">
        <section className="rs-panel">
          <h3><i className="bi bi-lightbulb" aria-hidden="true" /> Key findings</h3>
          <ul className="rs-findings">
            {report.findings.map((f, i) => (
              <li key={i} className={`rs-tone-${f.severity}`}>
                <i className={`bi ${SEVERITY_ICON[f.severity] || SEVERITY_ICON.info}`} aria-hidden="true" />
                <span>{f.text}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="rs-panel">
          <h3><i className="bi bi-list-check" aria-hidden="true" /> Recommended actions</h3>
          <ol className="rs-actions">
            {report.recommendations.map((r, i) => (
              <li key={i}>
                <span className={`rs-priority rs-priority-${r.priority}`}>{r.priority}</span>
                <span>{r.text}</span>
              </li>
            ))}
          </ol>
        </section>
      </div>

      {report.charts.length > 0 && <div className="rs-charts">{report.charts.map((c) => <StudioChart key={c.id} chart={c} />)}</div>}
      {report.tables.map((t) => <DataTable key={t.id} table={t} />)}

      {report.notes?.length > 0 && (
        <footer className="rs-footnotes">
          <b>How to read this report</b>
          <ul>{report.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          <small>
            Generated {new Date(report.generated_at).toLocaleString()}
            {scope.generated_by ? ` by ${scope.generated_by}` : ''}{scope.tenant ? ` · ${scope.tenant}` : ''}
          </small>
        </footer>
      )}
    </article>
  );
}
