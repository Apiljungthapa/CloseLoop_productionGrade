// Reusable report view: tabs, optional period/group filter, generic dynamic table, CSV download.
// Used by both SuperAdminReports and TeamLeadReports.
import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { Card, Tab, Nav, Table, Button, Form, Alert, Spinner, Badge } from 'react-bootstrap';
import { reportAPI, periodAPI, entityGroupAPI, entityAPI, userAPI } from '../apiEntities.js';

// Map a report tab key to backend kind + filter capability + icon
const REPORT_DEFS = {
  'current-period-status': {
    key: 'current-period-status',
    label: 'Current Period Status',
    icon: 'bi-clipboard-check',
    headerVariant: 'primary',
    supportsPeriodFilter: true,
    supportsGroupFilter: true,
    supportsEntityFilter: true,
    // No required filter — defaults to the period covering today.
    apiFn: reportAPI.currentPeriodStatus,
    description: 'Task-by-task status of every Close-Pack task in the current period, with headline counters',
    // Summary keys (in order) rendered as stat cards above the table.
    summaryCards: [
      ['total', 'Total Tasks'],
      ['completed', 'Completed'],
      ['completed_late', 'Completed Late'],
      ['pending', 'Pending'],
      ['in_progress', 'In Progress'],
      ['pending_review', 'Pending Review'],
      ['carried_forward', 'Carried Forward'],
      ['missed', 'Missed'],
      ['unassigned', 'Unassigned'],
    ],
  },
  'period-close-completion': {
    key: 'period-close-completion',
    label: 'Period Close Completion',
    icon: 'bi-check2-circle',
    headerVariant: 'primary',
    supportsPeriodFilter: true,
    supportsGroupFilter: true,
    supportsEntityFilter: true,
    requiresPeriodFilter: true, // period_id is required by the backend
    apiFn: reportAPI.periodCloseCompletion,
    description: 'Overall completion %, task breakdowns, review status for period close cycles',
  },
  'overdue-tasks': {
    key: 'overdue-tasks',
    label: 'Overdue Tasks',
    icon: 'bi-exclamation-triangle',
    headerVariant: 'danger',
    supportsPeriodFilter: true,
    apiFn: reportAPI.overdueTasks,
    description: 'Tasks past their due dates, sorted by most delayed first',
  },
  'user-workload': {
    key: 'user-workload',
    label: 'User Workload & Progress',
    icon: 'bi-person-check',
    headerVariant: 'info',
    supportsPeriodFilter: true,
    supportsUserFilter: true,
    requiresPeriodFilter: true, // period_id is required by the backend
    apiFn: reportAPI.userWorkload,
    description: 'Per-staff performance tracking, completion rates, hours logged',
  },
  'year-end-rollup': {
    key: 'year-end-rollup',
    label: 'Year-End Rollup',
    icon: 'bi-calendar-range',
    headerVariant: 'success',
    supportsEntityFilter: true,
    requiresEntityFilter: true,
    apiFn: reportAPI.yearEndRollup,
    description: 'Fiscal year aggregated trends, completion rates, historical data',
  },
  'time-entries': {
    key: 'time-entries',
    label: 'Time Entries',
    icon: 'bi-clock-history',
    headerVariant: 'warning',
    supportsDateRange: true,
    supportsEntityFilter: true,
    supportsUserFilter: true,
    supportsPeriodFilter: true,
    requiresDateRange: true, // start_date & end_date are required by the backend
    apiFn: reportAPI.timeEntries,
    description: 'Hours logged by staff, task type, and entity for billing',
  },
};

// Convert any error payload into a safe string (FastAPI 422 returns array of objects)
function formatErrorMessage(err, fallback) {
  const data = err?.response?.data;
  // Axios with responseType: 'blob' returns the error body as a Blob.
  // We surface a generic message in that case so we never try to render
  // a Blob — the caller can refresh in non-blob mode to see the detail.
  if (data instanceof Blob) {
    return `${fallback} (status ${err?.response?.status || 'unknown'})`;
  }
  const detail = data?.detail;
  if (!detail) return err?.message || fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (typeof d === 'string') return d;
        const loc = Array.isArray(d.loc) ? d.loc.join('.') : '';
        return loc ? `${loc}: ${d.msg}` : d.msg;
      })
      .filter(Boolean)
      .join('; ');
  }
  if (typeof detail === 'object') return JSON.stringify(detail);
  return String(detail);
}

// When a Blob-typed error response comes back (the file download path),
// attempt to read it as JSON so we can show the real backend detail.
async function readBlobError(err, fallback) {
  const data = err?.response?.data;
  if (data instanceof Blob) {
    try {
      const text = await data.text();
      try {
        const parsed = JSON.parse(text);
        if (parsed?.detail) {
          if (typeof parsed.detail === 'string') return parsed.detail;
          return JSON.stringify(parsed.detail);
        }
        return text || fallback;
      } catch {
        return text || fallback;
      }
    } catch {
      return fallback;
    }
  }
  return formatErrorMessage(err, fallback);
}

// Flatten group tree → flat list with depth for indented dropdown
function flattenGroupTree(nodes, depth = 0, acc = []) {
  for (const n of nodes || []) {
    acc.push({ id: n.id, name: n.name, depth, is_active: n.is_active !== false });
    if (Array.isArray(n.children) && n.children.length) {
      flattenGroupTree(n.children, depth + 1, acc);
    }
  }
  return acc;
}

function fmtCell(value) {
  if (value === null || value === undefined || value === '') return <span className="text-muted">—</span>;
  if (typeof value === 'number') {
    // If looks like a percentage (0..100 with column hint), still render as plain number;
    // higher-level: just render directly.
    return Number.isInteger(value) ? value : value.toFixed(2);
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  // ISO date?
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    try { return new Date(value).toLocaleString(); } catch { return value; }
  }
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    try { return new Date(value).toLocaleDateString(); } catch { return value; }
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function prettifyCol(c) {
  return String(c).replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
}

export default function ReportView({ availableReports, title, subtitle, defaultReport }) {
  const tabs = availableReports.map((k) => REPORT_DEFS[k]).filter(Boolean);
  const [activeKey, setActiveKey] = useState(defaultReport || tabs[0]?.key);
  const [periodId, setPeriodId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [entityId, setEntityId] = useState('');
  const [userId, setUserId] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [activeOnly, setActiveOnly] = useState(false);
  const [periods, setPeriods] = useState([]);
  const [groupTree, setGroupTree] = useState([]);
  const [entities, setEntities] = useState([]);
  const [users, setUsers] = useState([]);
  const [reports, setReports] = useState({}); // { key: { data, columns, generated_at } }
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [csvBusy, setCsvBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);

  // Load periods + groups + entities + users once for filter dropdowns
  useEffect(() => {
    (async () => {
      try {
        const { data } = await periodAPI.list();
        const list = data || [];
        setPeriods(list);
        // Auto-select a sensible default period so reports that require
        // period_id can load immediately. Prefer current/active period if
        // exposed via `is_current` or `status === 'OPEN'`, otherwise fall
        // back to the most recent period.
        if (list.length && !periodId) {
          const current =
            list.find((p) => p.is_current) ||
            list.find((p) => (p.status || '').toUpperCase() === 'OPEN') ||
            list[0];
          if (current?.id) setPeriodId(current.id);
        }
      } catch {
        // non-fatal
      }
    })();
    (async () => {
      try {
        const { data } = await entityGroupAPI.tree({ include_inactive: false });
        setGroupTree(data || []);
      } catch {
        // non-fatal
      }
    })();
    (async () => {
      try {
        const { data } = await entityAPI.list();
        setEntities(data || []);
      } catch {
        // non-fatal
      }
    })();
    (async () => {
      try {
        const { data } = await userAPI.list();
        setUsers(data || []);
      } catch {
        // non-fatal
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const flatGroups = useMemo(() => flattenGroupTree(groupTree), [groupTree]);

  const activeDef = REPORT_DEFS[activeKey];

  // Check whether all required params are provided. Returns a friendly
  // message when something is missing, or null when we are ready to call.
  const missingRequiredMessage = useMemo(() => {
    if (!activeDef) return null;
    if (activeDef.requiresPeriodFilter && !periodId) {
      return 'Select a period to load this report.';
    }
    if (activeDef.requiresEntityFilter && !entityId) {
      return 'Select an entity to load this report.';
    }
    if (activeDef.requiresDateRange && (!startDate || !endDate)) {
      return 'Pick a start and end date to load this report.';
    }
    return null;
  }, [activeDef, periodId, entityId, startDate, endDate]);

  const buildParams = useCallback(() => {
    const params = {};
    if (!activeDef) return params;
    if (activeDef.supportsPeriodFilter && periodId) params.period_id = periodId;
    if (activeDef.supportsGroupFilter && groupId) params.group_id = groupId;
    if (activeDef.supportsEntityFilter && entityId) params.entity_id = entityId;
    if (activeDef.supportsUserFilter && userId) params.target_user_id = userId;
    if (activeDef.supportsDateRange) {
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
    }
    if (activeDef.supportsActiveOnly && activeOnly) params.active_only = true;
    return params;
  }, [activeDef, periodId, groupId, entityId, userId, startDate, endDate, activeOnly]);

  const loadReport = useCallback(async () => {
    if (!activeDef) return;
    // Don't hit the backend until required params are provided
    if (missingRequiredMessage) {
      setError(null);
      // Clear any stale data from a previous tab/load
      setReports((prev) => ({ ...prev, [activeKey]: null }));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const { data } = await activeDef.apiFn(buildParams());
      setReports((prev) => ({ ...prev, [activeKey]: data }));
    } catch (err) {
      setError(formatErrorMessage(err, `Failed to load ${activeDef.label} report`));
    } finally {
      setLoading(false);
    }
  }, [activeDef, activeKey, buildParams, missingRequiredMessage]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  async function handleDownloadCsv() {
    if (!activeDef) return;
    if (missingRequiredMessage) {
      setError(missingRequiredMessage);
      return;
    }
    setCsvBusy(true);
    try {
      const res = await reportAPI.downloadCsv(activeDef.key, buildParams());
      const blob = new Blob([res.data], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const stamp = new Date().toISOString().slice(0, 10);
      a.download = `${activeDef.key}_${stamp}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(await readBlobError(err, 'CSV download failed'));
    } finally {
      setCsvBusy(false);
    }
  }

  async function handleDownloadPdf() {
    if (!activeDef) return;
    if (missingRequiredMessage) {
      setError(missingRequiredMessage);
      return;
    }
    setPdfBusy(true);
    try {
      const res = await reportAPI.downloadPdf(activeDef.key, buildParams());
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const stamp = new Date().toISOString().slice(0, 10);
      a.download = `${activeDef.key}_${stamp}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(await readBlobError(err, 'PDF download failed'));
    } finally {
      setPdfBusy(false);
    }
  }

  const current = reports[activeKey];

  return (
    <>
      <div className="mb-4">
        <h2 className="dashboard-title mb-2">
          <i className="bi bi-file-earmark-bar-graph me-2 text-primary"></i>
          {title}
        </h2>
        {subtitle && <p className="text-muted mb-0">{subtitle}</p>}
      </div>

      {error && <Alert variant="danger" dismissible onClose={() => setError(null)}>{error}</Alert>}

      <Tab.Container activeKey={activeKey} onSelect={(k) => setActiveKey(k)}>
        <div className="d-flex flex-wrap gap-3 align-items-center mb-3">
          <Nav variant="pills" className="flex-wrap gap-2">
            {tabs.map((t) => (
              <Nav.Item key={t.key}>
                <Nav.Link eventKey={t.key}>
                  <i className={`bi ${t.icon} me-1`}></i> {t.label}
                </Nav.Link>
              </Nav.Item>
            ))}
          </Nav>

          <div className="ms-auto d-flex gap-2 align-items-center flex-wrap">
            {activeDef?.supportsPeriodFilter && (
              <Form.Select
                size="sm"
                value={periodId}
                onChange={(e) => setPeriodId(e.target.value)}
                style={{ maxWidth: 220 }}
                title="Period filter"
              >
                <option value="">All Periods</option>
                {periods.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label || `${p.start_date} → ${p.end_date}`}
                  </option>
                ))}
              </Form.Select>
            )}
            {activeDef?.supportsGroupFilter && (
              <Form.Select
                size="sm"
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
                style={{ maxWidth: 240 }}
                title="Entity group filter"
              >
                <option value="">All Groups</option>
                {flatGroups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {Array(g.depth).fill('\u00A0\u00A0').join('')}{g.name}
                  </option>
                ))}
              </Form.Select>
            )}
            {activeDef?.supportsEntityFilter && (
              <Form.Select
                size="sm"
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
                style={{ maxWidth: 240 }}
                title={activeDef.requiresEntityFilter ? "Entity (required)" : "Entity filter"}
                className={activeDef.requiresEntityFilter && !entityId ? 'border-danger' : ''}
              >
                <option value="">{activeDef.requiresEntityFilter ? 'Select Entity...' : 'All Entities'}</option>
                {entities.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </Form.Select>
            )}
            {activeDef?.supportsUserFilter && (
              <Form.Select
                size="sm"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                style={{ maxWidth: 200 }}
                title="User filter"
              >
                <option value="">All Users</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </Form.Select>
            )}
            {activeDef?.supportsDateRange && (
              <>
                <Form.Control
                  type="date"
                  size="sm"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  style={{ maxWidth: 160 }}
                  title="Start date"
                  className={activeDef.key === 'time-entries' && !startDate ? 'border-danger' : ''}
                />
                <Form.Control
                  type="date"
                  size="sm"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  style={{ maxWidth: 160 }}
                  title="End date"
                  className={activeDef.key === 'time-entries' && !endDate ? 'border-danger' : ''}
                />
              </>
            )}
            {activeDef?.supportsActiveOnly && (
              <Form.Check
                type="switch"
                id="active-only-switch"
                label="Active only"
                checked={activeOnly}
                onChange={(e) => setActiveOnly(e.target.checked)}
                className="small"
              />
            )}
            <Button
              size="sm"
              variant="outline-secondary"
              onClick={loadReport}
              disabled={loading}
              title="Refresh"
            >
              <i className="bi bi-arrow-clockwise"></i>
            </Button>
            <Button
              size="sm"
              variant="success"
              onClick={handleDownloadCsv}
              disabled={csvBusy || !current}
            >
              {csvBusy ? (
                <Spinner animation="border" size="sm" />
              ) : (
                <>
                  <i className="bi bi-download me-1"></i> CSV
                </>
              )}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={handleDownloadPdf}
              disabled={pdfBusy || !current}
            >
              {pdfBusy ? (
                <Spinner animation="border" size="sm" />
              ) : (
                <>
                  <i className="bi bi-file-pdf me-1"></i> PDF
                </>
              )}
            </Button>
          </div>
        </div>

        <Tab.Content>
          <Tab.Pane eventKey={activeKey} active>
            <Card className="shadow-sm">
              <Card.Header className={`bg-${activeDef?.headerVariant || 'primary'} text-white d-flex justify-content-between align-items-center`}>
                <h5 className="mb-0">
                  <i className={`bi ${activeDef?.icon} me-2`}></i>
                  {activeDef?.label} Report
                </h5>
                <div className="d-flex align-items-center gap-2">
                  {current && (
                    <Badge bg="light" text="dark">
                      {(current.rows?.length ?? current.data?.length ?? 0)} rows
                    </Badge>
                  )}
                  {activeDef?.description && (
                    <Button
                      size="sm"
                      variant="outline-light"
                      title={activeDef.description}
                    >
                      <i className="bi bi-info-circle"></i>
                    </Button>
                  )}
                </div>
              </Card.Header>
              <Card.Body className="p-0">
                {activeDef?.summaryCards && current?.summary && !missingRequiredMessage && !loading && (
                  <div className="p-3 border-bottom bg-light">
                    {(current.summary.period_label || current.summary.bs_start_label) && (
                      <div className="small text-muted mb-2">
                        <i className="bi bi-calendar3 me-1"></i>
                        {current.summary.period_label}
                        {current.summary.bs_start_label ? ` · ${current.summary.bs_start_label}` : ''}
                      </div>
                    )}
                    <div className="d-flex flex-wrap gap-2">
                      {activeDef.summaryCards.map(([k, lbl]) => (
                        <div key={k} className="border rounded px-3 py-2 bg-white" style={{ minWidth: 110 }}>
                          <div className="text-muted" style={{ fontSize: 11 }}>{lbl}</div>
                          <div className="fw-bold" style={{ fontSize: 18 }}>{current.summary[k] ?? 0}</div>
                        </div>
                      ))}
                      <div className="border rounded px-3 py-2 bg-white" style={{ minWidth: 110 }}>
                        <div className="text-muted" style={{ fontSize: 11 }}>Completion</div>
                        <div className="fw-bold text-primary" style={{ fontSize: 18 }}>
                          {Math.round((current.summary.completion_pct || 0) * 100)}%
                        </div>
                      </div>
                    </div>
                  </div>
                )}
                {missingRequiredMessage ? (
                  <Alert variant="warning" className="mb-0 border-0 text-center py-5">
                    <i className="bi bi-funnel me-2"></i>
                    {missingRequiredMessage}
                  </Alert>
                ) : loading ? (
                  <div className="text-center py-5">
                    <Spinner animation="border" variant="primary" />
                    <p className="mt-2 text-muted">Loading report…</p>
                  </div>
                ) : !current ? (
                  <Alert variant="light" className="mb-0 border-0 text-center text-muted py-5">
                    Select filters and refresh to load a report.
                  </Alert>
                ) : (current.rows ?? current.data ?? []).length === 0 ? (
                  <Alert variant="info" className="mb-0 border-0 text-center py-5">
                    <i className="bi bi-inbox me-2"></i>
                    No data available for this report.
                  </Alert>
                ) : (
                  <div className="table-responsive">
                    <Table hover className="mb-0 align-middle">
                      <thead className="table-light">
                        <tr>
                          {(current.columns || []).map((c) => (
                            <th key={c} className="text-nowrap">{prettifyCol(c)}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(current.rows ?? current.data ?? []).map((row, idx) => (
                          <tr key={idx}>
                            {(current.columns || []).map((c) => (
                              <td key={c} className="text-nowrap small">
                                {fmtCell(row?.[c])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                  </div>
                )}
              </Card.Body>
              {current?.generated_at && (
                <Card.Footer className="bg-light text-muted small">
                  Generated {new Date(current.generated_at).toLocaleString()}
                </Card.Footer>
              )}
            </Card>
          </Tab.Pane>
        </Tab.Content>
      </Tab.Container>
    </>
  );
}
