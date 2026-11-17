import React, { useState, useEffect, useCallback } from 'react';
import { Card, Alert, Tab, Nav, Button, Table, Form, Spinner, Badge } from 'react-bootstrap';
import { reportAPI, periodAPI, entityGroupAPI, entityAPI, userAPI } from '../apiEntities.js';

// Safe report definitions with fallback functions
const REPORT_DEFS = {
  'period-close-completion': {
    key: 'period-close-completion',
    label: 'Period Close Completion',
    icon: 'bi-check2-circle',
    headerVariant: 'primary',
    supportsPeriodFilter: true,
    supportsGroupFilter: true,
    supportsEntityFilter: true,
    apiFn: async (params) => {
      if (reportAPI?.periodCloseCompletion) {
        return reportAPI.periodCloseCompletion(params);
      }
      return { data: { data: [], columns: [], generated_at: new Date().toISOString() } };
    },
    description: 'Overall completion %, task breakdowns, review status for period close cycles',
  },
  'overdue-tasks': {
    key: 'overdue-tasks',
    label: 'Overdue Tasks',
    icon: 'bi-exclamation-triangle',
    headerVariant: 'danger',
    supportsPeriodFilter: true,
    apiFn: async (params) => {
      if (reportAPI?.overdueTasks) {
        return reportAPI.overdueTasks(params);
      }
      return { data: { data: [], columns: [], generated_at: new Date().toISOString() } };
    },
    description: 'Tasks past their due dates, sorted by most delayed first',
  },
  'user-workload': {
    key: 'user-workload',
    label: 'User Workload & Progress',
    icon: 'bi-person-check',
    headerVariant: 'info',
    supportsPeriodFilter: true,
    supportsUserFilter: true,
    apiFn: async (params) => {
      if (reportAPI?.userWorkload) {
        return reportAPI.userWorkload(params);
      }
      return { data: { data: [], columns: [], generated_at: new Date().toISOString() } };
    },
    description: 'Per-staff performance tracking, completion rates, hours logged',
  },
  'year-end-rollup': {
    key: 'year-end-rollup',
    label: 'Year-End Rollup',
    icon: 'bi-calendar-range',
    headerVariant: 'success',
    supportsEntityFilter: true,
    requiresEntityFilter: true,
    apiFn: async (params) => {
      if (reportAPI?.yearEndRollup) {
        return reportAPI.yearEndRollup(params);
      }
      return { data: { data: [], columns: [], generated_at: new Date().toISOString() } };
    },
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
    apiFn: async (params) => {
      if (reportAPI?.timeEntries) {
        return reportAPI.timeEntries(params);
      }
      return { data: { data: [], columns: [], generated_at: new Date().toISOString() } };
    },
    description: 'Hours logged by staff, task type, and entity for billing',
  },
};

// Utility functions
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
  if (value === null || value === undefined || value === '') {
    return <span className="text-muted">—</span>;
  }
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value : value.toFixed(2);
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
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

export default function ReportViewProgressive({ availableReports, title, subtitle, defaultReport }) {
  const [activeKey, setActiveKey] = useState(null);
  const [reports, setReports] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [entities, setEntities] = useState([]);
  const [users, setUsers] = useState([]);
  const [groupTree, setGroupTree] = useState([]);
  
  // Filter states
  const [periodId, setPeriodId] = useState('');
  const [entityId, setEntityId] = useState('');
  const [userId, setUserId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  // Initialize tabs and set default
  useEffect(() => {
    try {
      const tabs = availableReports.map((k) => REPORT_DEFS[k]).filter(Boolean);
      if (tabs.length > 0) {
        const initialTab = defaultReport || tabs[0]?.key;
        setActiveKey(initialTab);
      }
    } catch (err) {
      console.error('Error initializing tabs:', err);
      setError(err.message);
    }
  }, [availableReports, defaultReport]);

  // Load dropdown data safely
  useEffect(() => {
    const loadDropdownData = async () => {
      try {
        // Load all dropdown data in parallel, handling failures gracefully
        const promises = [
          periodAPI?.list?.() || Promise.resolve({ data: [] }),
          entityAPI?.list?.() || Promise.resolve({ data: [] }),
          userAPI?.list?.() || Promise.resolve({ data: [] }),
          entityGroupAPI?.tree?.({ include_inactive: false }) || Promise.resolve({ data: [] }),
        ];

        const results = await Promise.allSettled(promises);
        
        if (results[0].status === 'fulfilled') {
          setPeriods(results[0].value?.data || []);
        }
        if (results[1].status === 'fulfilled') {
          setEntities(results[1].value?.data || []);
        }
        if (results[2].status === 'fulfilled') {
          setUsers(results[2].value?.data || []);
        }
        if (results[3].status === 'fulfilled') {
          setGroupTree(results[3].value?.data || []);
        }
      } catch (err) {
        console.warn('Error loading dropdown data:', err);
        // Don't show error for dropdown failures, just continue with empty arrays
      }
    };

    loadDropdownData();
  }, []);

  const loadReport = useCallback(async (reportKey) => {
    const reportDef = REPORT_DEFS[reportKey];
    if (!reportDef) return;

    // Check required filters before loading
    if (reportDef.requiresEntityFilter && !entityId) {
      setReports(prev => ({
        ...prev,
        [reportKey]: { data: [], columns: [], generated_at: new Date().toISOString() }
      }));
      return;
    }

    if (reportKey === 'time-entries' && (!startDate || !endDate)) {
      setReports(prev => ({
        ...prev,
        [reportKey]: { data: [], columns: [], generated_at: new Date().toISOString() }
      }));
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const params = {};
      if (reportDef.supportsPeriodFilter && periodId) params.period_id = periodId;
      if (reportDef.supportsGroupFilter && groupId) params.group_id = groupId;
      if (reportDef.supportsEntityFilter && entityId) params.entity_id = entityId;
      if (reportDef.supportsUserFilter && userId) params.target_user_id = userId;
      if (reportDef.supportsDateRange && startDate && endDate) {
        params.start_date = startDate;
        params.end_date = endDate;
      }

      const response = await reportDef.apiFn(params);
      
      // Handle different response formats
      let reportData = response?.data || response;
      if (!reportData || (!reportData.data && !reportData.rows)) {
        reportData = { data: [], columns: [], generated_at: new Date().toISOString() };
      }

      setReports(prev => ({ ...prev, [reportKey]: reportData }));
    } catch (err) {
      console.error('Report loading error:', err);
      setError(`Failed to load ${reportDef.label}: ${err.message || 'Unknown error'}`);
      setReports(prev => ({
        ...prev,
        [reportKey]: { data: [], columns: [], generated_at: new Date().toISOString() }
      }));
    } finally {
      setLoading(false);
    }
  }, [periodId, entityId, userId, groupId, startDate, endDate]);

  // Load report when active tab or filters change
  useEffect(() => {
    if (activeKey) {
      loadReport(activeKey);
    }
  }, [activeKey, loadReport]);

  const handleTabChange = (key) => {
    setActiveKey(key);
    setError(null); // Clear errors when switching tabs
  };

  try {
    const tabs = availableReports.map((k) => REPORT_DEFS[k]).filter(Boolean);
    const activeDef = REPORT_DEFS[activeKey];
    const current = reports[activeKey];
    const flatGroups = flattenGroupTree(groupTree);

    return (
      <div className="mb-4">
        <h2 className="dashboard-title mb-2">
          <i className="bi bi-file-earmark-bar-graph me-2 text-primary"></i>
          {title || 'Reports'}
        </h2>
        {subtitle && <p className="text-muted mb-0">{subtitle}</p>}
        
        {error && (
          <Alert variant="danger" dismissible onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Tab.Container activeKey={activeKey} onSelect={handleTabChange}>
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
                  style={{ maxWidth: 180 }}
                >
                  <option value="">All Periods</option>
                  {periods.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label || `${p.start_date} → ${p.end_date}`}
                    </option>
                  ))}
                </Form.Select>
              )}

              {activeDef?.supportsEntityFilter && (
                <Form.Select
                  size="sm"
                  value={entityId}
                  onChange={(e) => setEntityId(e.target.value)}
                  style={{ maxWidth: 180 }}
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
                  style={{ maxWidth: 160 }}
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
                    style={{ maxWidth: 140 }}
                    className={activeDef.key === 'time-entries' && !startDate ? 'border-danger' : ''}
                  />
                  <Form.Control
                    type="date"
                    size="sm"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    style={{ maxWidth: 140 }}
                    className={activeDef.key === 'time-entries' && !endDate ? 'border-danger' : ''}
                  />
                </>
              )}

              <Button
                size="sm"
                variant="outline-secondary"
                onClick={() => loadReport(activeKey)}
                disabled={loading}
              >
                <i className="bi bi-arrow-clockwise"></i>
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
                        {(current.data?.length || current.rows?.length || 0)} rows
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
                  {loading ? (
                    <div className="text-center py-5">
                      <Spinner animation="border" variant="primary" />
                      <p className="mt-2 text-muted">Loading report…</p>
                    </div>
                  ) : !current ? (
                    <Alert variant="light" className="mb-0 border-0 text-center text-muted py-5">
                      <i className="bi bi-info-circle me-2"></i>
                      Select filters and click refresh to load report data.
                    </Alert>
                  ) : activeDef?.requiresEntityFilter && !entityId ? (
                    <Alert variant="warning" className="mb-0 border-0 text-center py-5">
                      <i className="bi bi-exclamation-triangle me-2"></i>
                      Please select an entity to view this report.
                    </Alert>
                  ) : activeDef?.key === 'time-entries' && (!startDate || !endDate) ? (
                    <Alert variant="warning" className="mb-0 border-0 text-center py-5">
                      <i className="bi bi-exclamation-triangle me-2"></i>
                      Please select a date range to view time entries.
                    </Alert>
                  ) : (current.data?.length === 0 || current.rows?.length === 0) ? (
                    <Alert variant="info" className="mb-0 border-0 text-center py-5">
                      <i className="bi bi-inbox me-2"></i>
                      No data available for this report with the selected filters.
                    </Alert>
                  ) : (
                    <div className="table-responsive">
                      <Table hover className="mb-0 align-middle">
                        <thead className="table-light">
                          <tr>
                            {(current.columns || []).map((c, idx) => (
                              <th key={`col-${idx}`} className="text-nowrap">
                                {prettifyCol(c)}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(current.data || current.rows || []).map((row, rowIdx) => (
                            <tr key={`row-${rowIdx}`}>
                              {(current.columns || []).map((c, colIdx) => (
                                <td key={`cell-${rowIdx}-${colIdx}`} className="text-nowrap small">
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
      </div>
    );
  } catch (err) {
    console.error('ReportViewProgressive render error:', err);
    return (
      <Alert variant="danger">
        <h4>Render Error</h4>
        <p>{err.message}</p>
        <Button variant="outline-danger" onClick={() => window.location.reload()}>
          Reload Page
        </Button>
      </Alert>
    );
  }
}
}